# SAPV Chatapp (Open WebUI Fork)

## Lokale Entwicklung (Frontend + Backend)

Zwei Terminals im Projektroot.

**1. Backend** (Python, Port 8080)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
./dev.sh
```

`dev.sh` setzt CORS für den Vite-Dev-Server und startet Uvicorn mit Reload.

**2. Frontend** (SvelteKit / Vite, Port 5173)

```bash
npm install
npm run dev
```

Die Oberfläche erreichst du unter [http://localhost:5173](http://localhost:5173); die API läuft auf [http://localhost:8080](http://localhost:8080).

## Änderungen vom Upstream (Open WebUI) holen

Remote `upstream` einmalig setzen (falls noch nicht vorhanden):

```bash
git remote add upstream https://github.com/open-webui/open-webui.git
```

Eigene Änderungen committen oder stashen, dann:

```bash
# 1. Upstream holen
git fetch upstream

# 2. Rebase
git rebase upstream/main

# 3. Push
git push origin main --force-with-lease
```

Falls Konflikte:

Beim Rebase liegen deine Commits **oben** auf dem aktuellen Upstream – genau das willst du. In Konflikt-Dateien gilt:

| Marker | Bedeutung beim Rebase |
|--------|------------------------|
| `<<<<<<< HEAD` | Upstream (Open WebUI) |
| `=======` | Trennlinie |
| `>>>>>>> dein-commit` | **Deine Änderung** |

**Eigene Version behalten** (ganze Datei):

```bash
git checkout --theirs pfad/zur/datei
git add pfad/zur/datei
git rebase --continue
```

> Beim Rebase ist `--theirs` deine Version und `--ours` der Upstream – genau andersherum als beim Merge.

**Upstream-Version behalten** (ganze Datei):

```bash
git checkout --ours pfad/zur/datei
git add pfad/zur/datei
git rebase --continue
```

**Manuell lösen:** Konfliktmarker entfernen und den gewünschten Code (oder eine Mischung) stehen lassen, dann:

```bash
git add .
git rebase --continue
```

**Automatisch deine Version bevorzugen** (bei vielen ähnlichen Konflikten):

```bash
git rebase -X theirs upstream/main
```

Rebase abbrechen und zum vorherigen Stand zurück:

```bash
git rebase --abort
```
