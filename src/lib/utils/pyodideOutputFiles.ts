import { WEBUI_API_BASE_URL } from '$lib/constants';
import { uploadFile } from '$lib/apis/files';

export type PyodideFileEntry = {
	path: string;
	name: string;
	size: number;
};

export type UploadedPyodideFile = {
	name: string;
	url: string;
	type: string;
};

export type PyodideFileSnapshot = Map<string, number>;

type WorkerMessage = Record<string, unknown>;

const UPLOADS_DIR = '/mnt/uploads';

const sendWorkerMessage = (worker: Worker, msg: WorkerMessage): Promise<any> => {
	const id = `fs-${Date.now()}-${Math.random().toString(36).slice(2)}`;
	return new Promise((resolve, reject) => {
		const timeout = setTimeout(() => {
			worker.removeEventListener('message', handler);
			reject(new Error('Pyodide worker timeout'));
		}, 30000);

		function handler(event: MessageEvent) {
			if (event.data?.id !== id) return;
			clearTimeout(timeout);
			worker.removeEventListener('message', handler);
			if (event.data?.error) {
				reject(new Error(event.data.error));
				return;
			}
			resolve(event.data);
		}

		worker.addEventListener('message', handler);
		worker.postMessage({ ...msg, id });
	});
};

const listDirectory = async (worker: Worker, path: string) => {
	const res = await sendWorkerMessage(worker, { type: 'fs:list', path });
	return (res.entries ?? []) as Array<{ name: string; type: 'file' | 'directory'; size: number }>;
};

export const listPyodideUploadFiles = async (worker: Worker, dir = UPLOADS_DIR): Promise<PyodideFileEntry[]> => {
	const files: PyodideFileEntry[] = [];

	const walk = async (path: string) => {
		const entries = await listDirectory(worker, path);
		for (const entry of entries) {
			const entryPath = `${path}/${entry.name}`.replace(/\/+/g, '/');
			if (entry.type === 'directory') {
				await walk(entryPath);
			} else {
				files.push({
					path: entryPath,
					name: entry.name,
					size: entry.size
				});
			}
		}
	};

	await walk(dir);
	return files;
};

export const snapshotPyodideUploadFiles = async (worker: Worker): Promise<PyodideFileSnapshot> => {
	const files = await listPyodideUploadFiles(worker);
	const snapshot = new Map<string, number>();
	for (const file of files) {
		snapshot.set(file.path, file.size);
	}
	return snapshot;
};

const guessMimeType = (filename: string) => {
	const ext = filename.split('.').pop()?.toLowerCase() ?? '';
	const map: Record<string, string> = {
		csv: 'text/csv',
		txt: 'text/plain',
		json: 'application/json',
		pdf: 'application/pdf',
		png: 'image/png',
		jpg: 'image/jpeg',
		jpeg: 'image/jpeg',
		gif: 'image/gif',
		webp: 'image/webp',
		svg: 'image/svg+xml',
		docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
		xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
		zip: 'application/zip',
		html: 'text/html',
		xml: 'text/xml',
		md: 'text/markdown'
	};
	return map[ext] ?? 'application/octet-stream';
};

const readPyodideFile = async (worker: Worker, path: string): Promise<ArrayBuffer> => {
	const res = await sendWorkerMessage(worker, { type: 'fs:read', path });
	return res.data as ArrayBuffer;
};

export const uploadNewPyodideFiles = async (
	worker: Worker,
	token: string,
	before: PyodideFileSnapshot,
	metadata?: object | null
): Promise<UploadedPyodideFile[]> => {
	if (!token) return [];

	const uploaded: UploadedPyodideFile[] = [];
	const after = await listPyodideUploadFiles(worker);

	for (const file of after) {
		const previousSize = before.get(file.path);
		if (previousSize === file.size) continue;

		try {
			const data = await readPyodideFile(worker, file.path);
			const mimeType = guessMimeType(file.name);
			const blob = new Blob([data], { type: mimeType });
			const upload = await uploadFile(token, new File([blob], file.name, { type: mimeType }), metadata, false);
			if (!upload?.id) continue;

			const isImage = mimeType.startsWith('image/');
			uploaded.push({
				name: file.name,
				url: `${WEBUI_API_BASE_URL}/files/${upload.id}/content`,
				type: isImage ? 'image' : 'file'
			});
		} catch (error) {
			console.error('Failed to upload Pyodide output file:', file.path, error);
		}
	}

	return uploaded;
};
