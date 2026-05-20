import asyncio
import json
import logging
import re
import uuid
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import websockets
from open_webui.env import AIOHTTP_CLIENT_ALLOW_REDIRECTS
from pydantic import BaseModel

from open_webui.env import AIOHTTP_CLIENT_ALLOW_REDIRECTS, WEB_DOMAIN

logger = logging.getLogger(__name__)


class ResultModel(BaseModel):
    """
    Execute Code Result Model
    """

    stdout: Optional[str] = ''
    stderr: Optional[str] = ''
    result: Optional[str] = ''


class JupyterCodeExecuter:
    """
    Execute code in jupyter notebook
    """

    def __init__(
        self,
        base_url: str,
        code: str,
        token: str = '',
        password: str = '',
        timeout: int = 60,
    ):
        """
        :param base_url: Jupyter server URL (e.g., "http://localhost:8888")
        :param code: Code to execute
        :param token: Jupyter authentication token (optional)
        :param password: Jupyter password (optional)
        :param timeout: WebSocket timeout in seconds (default: 60s)
        """
        self.base_url = base_url
        self.code = code
        self.token = token
        self.password = password
        self.timeout = timeout
        self.kernel_id = ''
        if self.base_url[-1] != '/':
            self.base_url += '/'
        self.session = aiohttp.ClientSession(trust_env=True, base_url=self.base_url)
        self.params = {}
        self.result = ResultModel()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.kernel_id:
            try:
                async with self.session.delete(f'api/kernels/{self.kernel_id}', params=self.params) as response:
                    response.raise_for_status()
            except Exception as err:
                logger.exception('close kernel failed, %s', err)
        await self.session.close()

    async def run(self) -> ResultModel:
        try:
            await self.sign_in()
            await self.init_kernel()
            await self.execute_code()
        except Exception as err:
            logger.exception('execute code failed, %s', err)
            self.result.stderr = f'Error: {err}'
        return self.result

    async def sign_in(self) -> None:
        # password authentication
        if self.password and not self.token:
            async with self.session.get('login') as response:
                response.raise_for_status()
                xsrf_token = response.cookies['_xsrf'].value
                if not xsrf_token:
                    raise ValueError('_xsrf token not found')
                self.session.cookie_jar.update_cookies(response.cookies)
                self.session.headers.update({'X-XSRFToken': xsrf_token})
            async with self.session.post(
                'login',
                data={'_xsrf': xsrf_token, 'password': self.password},
                allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS,
            ) as response:
                response.raise_for_status()
                self.session.cookie_jar.update_cookies(response.cookies)

        # token authentication
        if self.token:
            self.params.update({'token': self.token})

    async def init_kernel(self) -> None:
        async with self.session.post(url='api/kernels', params=self.params) as response:
            response.raise_for_status()
            kernel_data = await response.json()
            self.kernel_id = kernel_data['id']

    def init_ws(self) -> (str, dict):
        ws_base = self.base_url.replace('http', 'ws', 1)
        ws_params = '?' + '&'.join([f'{key}={val}' for key, val in self.params.items()])
        websocket_url = f'{ws_base}api/kernels/{self.kernel_id}/channels{ws_params if len(ws_params) > 1 else ""}'
        ws_headers = {}
        if self.password and not self.token:
            ws_headers = {
                'Cookie': '; '.join([f'{cookie.key}={cookie.value}' for cookie in self.session.cookie_jar]),
                **self.session.headers,
            }
        return websocket_url, ws_headers

    async def execute_code(self) -> None:
        # initialize ws
        websocket_url, ws_headers = self.init_ws()
        # execute
        async with websockets.connect(websocket_url, additional_headers=ws_headers) as ws:
            await self.execute_in_jupyter(ws)

    async def execute_in_jupyter(self, ws) -> None:
        # send message
        msg_id = uuid.uuid4().hex
        await ws.send(
            json.dumps(
                {
                    'header': {
                        'msg_id': msg_id,
                        'msg_type': 'execute_request',
                        'username': 'user',
                        'session': uuid.uuid4().hex,
                        'date': '',
                        'version': '5.3',
                    },
                    'parent_header': {},
                    'metadata': {},
                    'content': {
                        'code': self.code,
                        'silent': False,
                        'store_history': True,
                        'user_expressions': {},
                        'allow_stdin': False,
                        'stop_on_error': True,
                    },
                    'channel': 'shell',
                }
            )
        )
        # parse message
        stdout, stderr, result = '', '', []
        while True:
            try:
                # wait for message
                message = await asyncio.wait_for(ws.recv(), self.timeout)
                message_data = json.loads(message)
                # msg id not match, skip
                if message_data.get('parent_header', {}).get('msg_id') != msg_id:
                    continue
                # check message type
                msg_type = message_data.get('msg_type')
                match msg_type:
                    case 'stream':
                        if message_data['content']['name'] == 'stdout':
                            stdout += message_data['content']['text']
                        elif message_data['content']['name'] == 'stderr':
                            stderr += message_data['content']['text']
                    case 'execute_result' | 'display_data':
                        data = message_data['content']['data']
                        if 'image/png' in data:
                            result.append(f'data:image/png;base64,{data["image/png"]}')
                        elif 'text/plain' in data:
                            result.append(data['text/plain'])
                    case 'error':
                        stderr += '\n'.join(message_data['content']['traceback'])
                    case 'status':
                        if message_data['content']['execution_state'] == 'idle':
                            break

            except asyncio.TimeoutError:
                stderr += '\nExecution timed out.'
                break
        self.result.stdout = stdout.strip()
        self.result.stderr = stderr.strip()
        self.result.result = '\n'.join(result).strip() if result else ''


MARKDOWN_LINK_PATTERN = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
FILE_API_PATH_PATTERN = re.compile(r'/api/v1/files/[^\s\)\]]+')


def build_file_download_origin(web_domain: str | None = None) -> str:
    """Build origin for /api/v1/files/... URLs from WEB_DOMAIN (env) or override."""
    domain = (web_domain or WEB_DOMAIN or '').strip() or 'localhost'

    if domain.startswith(('http://', 'https://')):
        parsed = urlparse(domain)
        if parsed.scheme and parsed.netloc:
            return f'{parsed.scheme}://{parsed.netloc}'
        return domain.rstrip('/')

    if domain.startswith('localhost') or domain.startswith('127.0.0.1'):
        host = domain if ':' in domain else 'localhost:8080'
        return f'http://{host}'

    return f'https://{domain}'


def resolve_file_api_url(path_or_url: str, web_domain: str | None = None) -> str:
    """Build a correct public file download URL from a path or malformed absolute URL."""
    target = path_or_url.strip()
    if not target:
        return target

    if target.lower().startswith('sandbox:'):
        target = target.split(':', 1)[1]

    path_match = FILE_API_PATH_PATTERN.search(target)
    if not path_match:
        return path_or_url

    api_path = path_match.group(0)
    origin = build_file_download_origin(web_domain)
    if origin:
        return f'{origin}{api_path}'

    return api_path


def normalize_download_target(target: str, files: list | None = None, web_domain: str | None = None) -> str:
    target = target.strip()
    if target.lower().startswith('sandbox:'):
        target = target.split(':', 1)[1]

    if target.startswith(('http://', 'https://')) and '/api/v1/files/' in target:
        return resolve_file_api_url(target, web_domain)

    if target.startswith('/mnt/uploads/'):
        filename = target.rsplit('/', 1)[-1]
        for file_item in files or []:
            if not isinstance(file_item, dict):
                continue
            name = file_item.get('name', '')
            if name == filename or name.split('/')[-1] == filename:
                url = file_item.get('url', '')
                if url:
                    return resolve_file_api_url(url, web_domain)

    if target.startswith('/api/v1/files/'):
        return resolve_file_api_url(target, web_domain)

    return target


def sanitize_download_links(text: str, files: list | None = None, web_domain: str | None = None) -> str:
    if not text or not isinstance(text, str):
        return text

    def replace_link(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        return f'[{label}]({normalize_download_target(target, files, web_domain)})'

    text = MARKDOWN_LINK_PATTERN.sub(replace_link, text)
    text = re.sub(
        r'sandbox:(/api/v1/files/[^\s\)\]]+)',
        lambda match: normalize_download_target(match.group(1), files, web_domain),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'sandbox:(/mnt/uploads/[^\s\)\]]+)',
        lambda match: normalize_download_target(match.group(0), files, web_domain),
        text,
        flags=re.IGNORECASE,
    )
    return text


def collect_output_files(output: list) -> list:
    files: list = []
    seen_urls: set[str] = set()

    for item in output:
        if item.get('type') != 'function_call_output':
            continue

        for file_item in item.get('files') or []:
            if not isinstance(file_item, dict):
                continue
            url = file_item.get('url')
            if url and url not in seen_urls:
                seen_urls.add(url)
                files.append(file_item)

        for part in item.get('output') or []:
            text = part.get('text', '')
            if not isinstance(text, str) or not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            for file_item in parsed.get('files') or []:
                if not isinstance(file_item, dict):
                    continue
                url = file_item.get('url')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    files.append(file_item)

    return files


def _is_execute_code_payload(data: dict) -> bool:
    return isinstance(data, dict) and any(key in data for key in ('stdout', 'stderr', 'result', 'files'))


def finalize_execute_code_payload(data: dict, web_domain: str | None = None) -> dict:
    files = data.get('files')
    if not isinstance(files, list):
        files = []

    for file_item in files:
        if isinstance(file_item, dict) and file_item.get('url'):
            file_item['url'] = resolve_file_api_url(file_item['url'], web_domain)

    if files:
        stdout = data.get('stdout', '')
        if isinstance(stdout, str):
            download_lines = [
                f"- [{file_item.get('name', 'file')}]({file_item.get('url')})"
                for file_item in files
                if isinstance(file_item, dict) and file_item.get('url')
            ]
            if download_lines:
                files_section = 'Files:\n' + '\n'.join(download_lines)
                if files_section not in stdout:
                    data['stdout'] = '\n'.join(
                        part for part in [stdout, files_section] if part
                    ).strip()

    for key in ('stdout', 'stderr', 'result'):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = sanitize_download_links(value, files, web_domain)

    return data


def rewrite_output_download_links(output: list, web_domain: str | None = None) -> list:
    files = collect_output_files(output)

    for file_item in files:
        if isinstance(file_item, dict) and file_item.get('url'):
            file_item['url'] = resolve_file_api_url(file_item['url'], web_domain)

    for item in output:
        if item.get('type') == 'message':
            for part in item.get('content') or []:
                if isinstance(part.get('text'), str):
                    part['text'] = sanitize_download_links(part['text'], files, web_domain)
        elif item.get('type') == 'function_call_output':
            for part in item.get('output') or []:
                text = part.get('text')
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    part['text'] = sanitize_download_links(text, files, web_domain)
                    continue
                if _is_execute_code_payload(parsed):
                    part['text'] = json.dumps(
                        finalize_execute_code_payload(parsed, web_domain),
                        ensure_ascii=False,
                    )
                else:
                    part['text'] = sanitize_download_links(text, files, web_domain)
        elif item.get('type') == 'open_webui:code_interpreter':
            ci_output = item.get('output')
            if isinstance(ci_output, dict):
                item['output'] = finalize_execute_code_payload(ci_output, web_domain)

    return output


async def execute_code_jupyter(
    base_url: str, code: str, token: str = '', password: str = '', timeout: int = 60
) -> dict:
    async with JupyterCodeExecuter(base_url, code, token, password, timeout) as executor:
        result = await executor.run()
        return result.model_dump()
