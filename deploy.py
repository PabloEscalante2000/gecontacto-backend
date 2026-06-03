"""
Deploy script: Laravel app -> Hostinger shared hosting
Estrategia: SSH para setup, HTTP para transferir archivos grandes.
App:    /home/u286274846/contacto_app/
Public: /home/u286274846/domains/grupoeades.org/public_html/contacto/
"""

import io
import os
import sys
import zipfile
import hashlib
import urllib.request
import urllib.parse
import paramiko
from pathlib import Path

# ── Configuracion ──────────────────────────────────────────────────────────
SSH_HOST     = os.environ.get("SSH_HOST", "145.223.105.59")
SSH_PORT     = int(os.environ.get("SSH_PORT", 65002))
SSH_USER     = os.environ.get("SSH_USER", "u286274846")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "")

APP_REMOTE   = "/home/u286274846/contacto_app"
WEB_REMOTE   = "/home/u286274846/domains/grupoeades.org/public_html/contacto"
PHP_BIN      = "/opt/alt/php84/usr/bin/php"
BASE_URL     = "https://grupoeades.org/contacto"

LOCAL_ROOT   = Path(__file__).parent

APP_EXCLUDE_DIRS  = {".git", "node_modules", ".claude", "tests", "public"}
APP_EXCLUDE_FILES = {".env", ".env.local", ".env.production", "deploy.py",
                     "Thumbs.db", ".DS_Store", ".mcp.json"}

DEPLOY_SECRET = hashlib.sha256(SSH_PASSWORD.encode()).hexdigest()[:32]

ENV_PRODUCTION = f"""APP_NAME=GeContacto
APP_ENV=production
APP_KEY=base64:py/Mq+x7j94oZ8U3ptdzSOl8O/wjs+HBWS/Q5EjWhTE=
APP_DEBUG=false
APP_URL=https://grupoeades.org/contacto

LOG_CHANNEL=stack
LOG_LEVEL=error

DB_CONNECTION=sqlite
DB_DATABASE={APP_REMOTE}/database/database.sqlite

CACHE_DRIVER=file
SESSION_DRIVER=file
QUEUE_CONNECTION=sync
"""

INDEX_PHP = f"""<?php

use Illuminate\\Foundation\\Application;
use Illuminate\\Http\\Request;

define('LARAVEL_START', microtime(true));

if (file_exists($maintenance = '{APP_REMOTE}/storage/framework/maintenance.php')) {{
    require $maintenance;
}}

require '{APP_REMOTE}/vendor/autoload.php';

/** @var Application $app */
$app = require_once '{APP_REMOTE}/bootstrap/app.php';

$app->handleRequest(Request::capture());
"""

HTACCESS = """<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteCond %{REQUEST_FILENAME} !-d
    RewriteCond %{REQUEST_FILENAME} !-f
    RewriteRule ^ index.php [L]
</IfModule>

AddHandler application/x-httpd-php84 .php

<IfModule mod_headers.c>
    Header always set X-Content-Type-Options nosniff
</IfModule>
"""

RECEIVER_PHP = f"""<?php
// Deploy receiver - se autodestruye despues de usarse
$secret = '{DEPLOY_SECRET}';
$given  = $_SERVER['HTTP_X_DEPLOY_SECRET'] ?? '';
if (!hash_equals($secret, $given)) {{ http_response_code(403); die('Forbidden'); }}

$action = $_GET['action'] ?? '';
$tmpDir = sys_get_temp_dir();

if ($action === 'ping') {{
    echo 'pong';
    exit;
}}

// Recibe un chunk y lo guarda en /tmp
if ($action === 'chunk') {{
    $id    = preg_replace('/[^a-z0-9]/', '', $_POST['id'] ?? '');
    $part  = (int)($_POST['part'] ?? 0);
    $data  = base64_decode($_POST['data'] ?? '');
    if (!$id || !$data) {{ http_response_code(400); die('Missing params'); }}
    file_put_contents("$tmpDir/deploy_{{$id}}_{{$part}}.chunk", $data);
    echo 'ok';
    exit;
}}

// Ensambla chunks y extrae el zip
if ($action === 'assemble') {{
    $id    = preg_replace('/[^a-z0-9]/', '', $_POST['id'] ?? '');
    $total = (int)($_POST['total'] ?? 0);
    $dest  = $_POST['dest'] ?? '';
    if (!$id || !$total || !$dest) {{ http_response_code(400); die('Missing params'); }}
    $zipPath = "$tmpDir/deploy_{{$id}}.zip";
    $fp = fopen($zipPath, 'wb');
    for ($i = 0; $i < $total; $i++) {{
        $chunk = "$tmpDir/deploy_{{$id}}_{{$i}}.chunk";
        fwrite($fp, file_get_contents($chunk));
        unlink($chunk);
    }}
    fclose($fp);
    $z = new ZipArchive();
    if ($z->open($zipPath) !== true) {{ http_response_code(500); die('Bad zip'); }}
    $count = $z->count();
    $z->extractTo($dest);
    $z->close();
    unlink($zipPath);
    echo 'ok:' . $count;
    exit;
}}

if ($action === 'cmd') {{
    $cmd = $_POST['cmd'] ?? '';
    if (!$cmd) {{ http_response_code(400); die('Missing cmd'); }}
    $out = shell_exec($cmd . ' 2>&1');
    echo $out;
    exit;
}}

if ($action === 'write') {{
    $path = $_POST['path'] ?? '';
    $data = $_POST['data'] ?? '';
    if (!$path) {{ http_response_code(400); die('Missing path'); }}
    file_put_contents($path, $data);
    echo 'ok';
    exit;
}}

if ($action === 'self_destruct') {{
    unlink(__FILE__);
    echo 'gone';
    exit;
}}

http_response_code(400);
die('Unknown action');
"""


# ── SSH helpers ───────────────────────────────────────────────────────────

def ssh_connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASSWORD, timeout=15)
    return c


def ssh_run(ssh, cmd):
    print(f"  $ {cmd[:100]}")
    _, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    for line in (out + "\n" + err).splitlines():
        if line.strip():
            print(f"    {line}")
    return out


def sftp_put_small(ssh, content: bytes, remote_path: str):
    """Sube un archivo pequeno (<100KB) via SFTP."""
    sftp = ssh.open_sftp()
    sftp.putfo(io.BytesIO(content), remote_path)
    sftp.close()


# ── HTTP helpers ──────────────────────────────────────────────────────────

CHUNK_SIZE = 800 * 1024  # 800 KB por chunk (deja margen para base64 overhead ~33%)


def http_post(action: str, data: dict = None) -> str:
    url = f"{BASE_URL}/_deploy_recv.php?action={action}"
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(url, data=body or None, headers={
        "X-Deploy-Secret": DEPLOY_SECRET,
        "Content-Type": "application/x-www-form-urlencoded",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read(500).decode()}")


def upload_zip_chunked(zip_bytes: bytes, dest: str, label: str) -> str:
    """Sube zip en chunks de ~800KB (base64-encoded) y ensambla en el servidor."""
    import base64
    chunks = [zip_bytes[i:i+CHUNK_SIZE] for i in range(0, len(zip_bytes), CHUNK_SIZE)]
    total = len(chunks)
    print(f"      {len(zip_bytes)//1024} KB en {total} chunks de ~{CHUNK_SIZE//1024} KB")
    for i, chunk in enumerate(chunks):
        encoded = base64.b64encode(chunk).decode()
        result = http_post("chunk", {"id": label, "part": str(i), "data": encoded})
        if result != "ok":
            raise RuntimeError(f"Chunk {i} fallo: {result}")
        print(f"      chunk {i+1}/{total} OK", end="\r", flush=True)
    print()
    result = http_post("assemble", {"id": label, "total": str(total), "dest": dest})
    return result


def http_cmd(cmd: str) -> str:
    return http_post("cmd", {"cmd": cmd})


# ── Zip builder ──────────────────────────────────────────────────────────

def build_zip(local_dir: Path, exclude_top: set, exclude_files: set) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in local_dir.rglob("*"):
            rel = path.relative_to(local_dir)
            parts = rel.parts
            if parts[0] in exclude_top:
                continue
            if path.name in exclude_files:
                continue
            if "storage" in parts and "logs" in parts and path.is_file():
                continue
            if path.is_file():
                zf.write(path, rel.as_posix())
    return buf.getvalue()


def main():
    print("\n=== Deploy GeContacto -> grupoeades.org/contacto ===\n")

    # ── 1. SSH: setup dirs + subir receiver ───────────────────────────────
    print("[1/6] Conectando por SSH y preparando servidor...")
    ssh = ssh_connect()
    ssh_run(ssh, f"mkdir -p {APP_REMOTE} {WEB_REMOTE}")
    ssh_run(ssh, f"mkdir -p {APP_REMOTE}/storage/app/public "
                 f"{APP_REMOTE}/storage/framework/cache/data "
                 f"{APP_REMOTE}/storage/framework/sessions "
                 f"{APP_REMOTE}/storage/framework/views "
                 f"{APP_REMOTE}/storage/logs "
                 f"{APP_REMOTE}/bootstrap/cache "
                 f"{APP_REMOTE}/database")
    # Subir receiver PHP al directorio web (es pequeño, cabe bien por SFTP)
    sftp_put_small(ssh, RECEIVER_PHP.encode(), f"{WEB_REMOTE}/_deploy_recv.php")
    print("      Receiver subido. Verificando...")
    ping = http_post("ping")
    if ping != "pong":
        print(f"      ERROR: receiver no responde ({ping})")
        sys.exit(1)
    print("      OK - receiver activo")

    # ── 2. Subir app via HTTP (chunked) ──────────────────────────────────
    print("[2/6] Comprimiendo y subiendo app (vendor incluido)...")
    app_zip = build_zip(LOCAL_ROOT, APP_EXCLUDE_DIRS, APP_EXCLUDE_FILES)
    result = upload_zip_chunked(app_zip, APP_REMOTE, "app")
    print(f"      {result}")

    # ── 3. Subir public/ via HTTP (chunked) ───────────────────────────────
    print("[3/6] Comprimiendo y subiendo public/...")
    pub_zip = build_zip(LOCAL_ROOT / "public", set(), {"index.php"})
    result = upload_zip_chunked(pub_zip, WEB_REMOTE, "pub")
    print(f"      {result}")

    # ── 4. Escribir index.php, .htaccess y .env via HTTP cmd ─────────────
    print("[4/6] Escribiendo index.php, .htaccess y .env...")
    http_post("write", {"path": f"{WEB_REMOTE}/index.php", "data": INDEX_PHP})
    http_post("write", {"path": f"{WEB_REMOTE}/.htaccess", "data": HTACCESS})
    http_post("write", {"path": f"{APP_REMOTE}/.env",      "data": ENV_PRODUCTION})
    print("      OK")

    # ── 5. Migraciones y cache ────────────────────────────────────────────
    print("[5/6] Migraciones y artisan cache...")
    out = http_cmd(f"touch {APP_REMOTE}/database/database.sqlite")
    out = http_cmd(f"cd {APP_REMOTE} && {PHP_BIN} artisan migrate --force 2>&1")
    print(f"      migrate: {out.strip()[:200]}")
    out = http_cmd(f"cd {APP_REMOTE} && {PHP_BIN} artisan config:cache 2>&1")
    print(f"      config:cache: {out.strip()[:100]}")
    out = http_cmd(f"cd {APP_REMOTE} && {PHP_BIN} artisan route:cache 2>&1")
    print(f"      route:cache: {out.strip()[:100]}")

    # ── 6. Permisos y destruir receiver ───────────────────────────────────
    print("[6/6] Permisos y limpieza...")
    ssh_run(ssh, f"chmod -R 775 {APP_REMOTE}/storage {APP_REMOTE}/bootstrap/cache")
    ssh_run(ssh, f"chmod 664 {APP_REMOTE}/database/database.sqlite")
    http_post("self_destruct")
    ssh.close()
    print("      OK")

    print("\n=== Deploy completado ===")
    print(f"    URL: https://grupoeades.org/contacto/api/user")
    print(f"    App: {APP_REMOTE}")
    print(f"    Web: {WEB_REMOTE}\n")


if __name__ == "__main__":
    if not SSH_PASSWORD:
        print("ERROR: Configura SSH_PASSWORD en .claude/settings.local.json")
        sys.exit(1)
    main()
