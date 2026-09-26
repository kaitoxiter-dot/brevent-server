import json, os, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE, 'data', 'brevent_db.json')
HTML     = os.path.join(BASE, 'index.html')

os.makedirs(os.path.join(BASE, 'data'), exist_ok=True)

# ── DB ────────────────────────────────────────────────────────────
def load_db():
    try:
        if os.path.exists(DB_FILE):
            d = json.loads(open(DB_FILE).read())
            if isinstance(d.get('keys'), list): return d
    except: pass
    return {'keys': []}

def save_db(d):
    open(DB_FILE, 'w').write(json.dumps(d, indent=2))

def is_expired(exp):
    if not exp: return False
    try: return datetime.fromisoformat(exp.replace('Z','')) < datetime.now()
    except: return False

def remaining(exp):
    if not exp: return 'Permanen (Lifetime)'
    try:
        diff = (datetime.fromisoformat(exp.replace('Z','')) - datetime.now()).days
        return 'Kadaluarsa' if diff < 0 else f'{diff} hari lagi'
    except: return '-'

def now_str():  return datetime.now().strftime('%H:%M:%S')
def date_str(): return datetime.now().strftime('%d/%m/%Y %H:%M:%S')

def add_log(key_obj, status, message):
    key_obj.setdefault('logs', []).insert(0, {
        'time': now_str(), 'status': status, 'message': message
    })
    key_obj['logs'] = key_obj['logs'][:100]

# ── HTML ──────────────────────────────────────────────────────────
def get_html():
    if not os.path.exists(HTML):
        return '<h1>Taruh index.html di folder yang sama</h1>'
    html = open(HTML, encoding='utf-8').read()
    inject = """
<script>
const _origSave = window.saveState;
window.saveState = function(s) {
  if (_origSave) _origSave(s);
  fetch('/api/state', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(s)
  }).catch(() => {});
};

async function syncFromServer() {
  try {
    const r = await fetch('/api/state');
    if (!r.ok) return;
    const s = await r.json();
    if (s && Array.isArray(s.keys)) {
      appState = s;
      if (!appState.activeTab) appState.activeTab = 'connected';
      if (_origSave) _origSave(appState);
      renderMain();
    }
  } catch(e) {}
}

window.addEventListener('load', () => setTimeout(syncFromServer, 400));
setInterval(syncFromServer, 10000);
</script>"""
    return html.replace('</body>', inject + '</body>')

# ── Request Handler ───────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        q    = {k: v[0] for k, v in parse_qs(p.query).items()}

        if path == '/':
            return self._html(get_html())

        # State sync
        if path == '/api/state':
            return self._json(200, load_db())

        # Status
        if path == '/api/status':
            db = load_db()
            keys = db.get('keys', [])
            active = sum(1 for k in keys if k.get('status') == 'active' and not is_expired(k.get('expiresAt')))
            return self._json(200, {
                'status': 'ONLINE', 'server_name': 'KTXSTABLE-SERVER', 'version': '2.0.0',
                'total_keys': len(keys), 'active_keys': active,
                'total_devices': sum(len(k.get('devices',[])) for k in keys),
                'total_banned_devices': sum(len(k.get('bannedDevices',[])) for k in keys),
                'server_time': datetime.now().isoformat()
            })

        # Keys list
        if path == '/api/keys':
            return self._json(200, {'status': 'success', 'keys': load_db().get('keys', [])})

        # Brevent check (plain text)
        if path == '/api/brevent/check':
            result = self._verify(q.get('key',''), q.get('sn') or q.get('serial',''), q.get('model',''))
            self.send_response(result['code'])
            body = result['body']['status'].encode()
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        # Brevent verify (JSON)
        if path == '/api/brevent/verify':
            result = self._verify(q.get('key',''), q.get('sn') or q.get('serial',''), q.get('model',''))
            return self._json(result['code'], result['body'])

        # Brevent script generator (untuk buyer)
        if path == '/api/brevent/script':
            host   = self.headers.get('Host', f'localhost:8080')
            key    = q.get('key', 'PASTE_KEY_ANDA').upper()
            script = self._gen_script(host, key)
            body   = script.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        self._json(404, {'error': 'not found'})

    def do_POST(self):
        p    = urlparse(self.path)
        path = p.path
        body = self._body()
        db   = load_db()

        # State save dari frontend
        if path == '/api/state':
            if isinstance(body, dict) and 'keys' in body:
                save_db(body)
            return self._json(200, {'status': 'ok'})

        # Create key
        if path == '/api/keys':
            code = (body.get('key') or '').strip().upper()
            if not code:
                return self._json(400, {'status': 'error', 'message': 'Kode key wajib diisi'})
            if any(k['key'].upper() == code for k in db.get('keys', [])):
                return self._json(400, {'status': 'error', 'message': 'Kode key sudah terdaftar'})
            new_key = {
                'id': code, 'key': code,
                'name': (body.get('name') or 'Tanpa Nama').strip(),
                'status': 'active',
                'createdAt': datetime.now().isoformat(),
                'expiresAt': body.get('expiresAt') or None,
                'maxDevices': int(body.get('maxDevices') or 0),
                'devices': [], 'bannedDevices': [],
                'logs': [{'time': now_str(), 'status': 'ok',
                          'message': f"Key dibuat untuk {body.get('name','User')}"}]
            }
            db.setdefault('keys', []).insert(0, new_key)
            save_db(db)
            return self._json(200, {'status': 'success', 'key': new_key})

        # Brevent verify POST
        if path == '/api/brevent/verify':
            result = self._verify(
                body.get('key',''), body.get('sn') or body.get('serial',''), body.get('model',''))
            return self._json(result['code'], result['body'])

        # Key actions: /api/keys/{id}/action
        m = re.match(r'^/api/keys/([^/]+)/([^/]+)$', path)
        if m:
            kid, action = m.group(1), m.group(2)
            target = next((k for k in db.get('keys',[]) if k['id'] == kid or k['key'] == kid), None)
            if not target:
                return self._json(404, {'status': 'error', 'message': 'Key tidak ditemukan'})

            if action == 'toggle':
                if target['status'] == 'active':
                    target['status'] = 'banned'
                    add_log(target, 'banned', 'Status key: OFF/BANNED')
                else:
                    target['status'] = 'active'
                    add_log(target, 'ok', 'Status key: ON/AKTIF')
                save_db(db)
                return self._json(200, {'status': 'success', 'new_status': target['status'], 'key': target})

            if action == 'expired':
                target['expiresAt'] = body.get('expiresAt') or None
                add_log(target, 'ok', f"Expired diperbarui: {target['expiresAt'] or 'Permanen'}")
                save_db(db)
                return self._json(200, {'status': 'success', 'key': target})

            if action == 'ban-device':
                sn = (body.get('serial') or '').strip()
                if not sn: return self._json(400, {'status': 'error', 'message': 'Serial wajib diisi'})
                target['devices'] = [d for d in target.get('devices',[]) if d['serial'] != sn]
                bans = target.setdefault('bannedDevices', [])
                if not any(b['serial'] == sn for b in bans):
                    bans.append({'serial': sn, 'reason': body.get('reason','Admin'),
                                 'bannedAt': date_str()})
                add_log(target, 'banned', f'Device {sn} dibanned')
                save_db(db)
                return self._json(200, {'status': 'success', 'key': target})

            if action == 'unban-device':
                sn = (body.get('serial') or '').strip()
                target['bannedDevices'] = [b for b in target.get('bannedDevices',[]) if b['serial'] != sn]
                add_log(target, 'ok', f'Device {sn} di-unban')
                save_db(db)
                return self._json(200, {'status': 'success', 'key': target})

            if action == 'unbind-device':
                sn = (body.get('serial') or '').strip()
                target['devices'] = [d for d in target.get('devices',[]) if d['serial'] != sn]
                add_log(target, 'ok', f'Device {sn} dilepas')
                save_db(db)
                return self._json(200, {'status': 'success', 'key': target})

            if action == 'reset-devices':
                target['devices'] = []
                add_log(target, 'ok', 'Semua device dilepas')
                save_db(db)
                return self._json(200, {'status': 'success', 'key': target})

        self._json(404, {'error': 'not found'})

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r'^/api/keys/([^/]+)$', path)
        if m:
            kid = m.group(1)
            db = load_db()
            before = len(db.get('keys', []))
            db['keys'] = [k for k in db.get('keys',[]) if k['id'] != kid and k['key'] != kid]
            if len(db['keys']) == before:
                return self._json(404, {'status': 'error', 'message': 'Key tidak ditemukan'})
            save_db(db)
            return self._json(200, {'status': 'success', 'message': 'Key dihapus'})
        self._json(404, {'error': 'not found'})

    # ── Verify Logic ──────────────────────────────────────────────
    def _verify(self, key, sn, model):
        key   = (key   or '').strip().upper()
        sn    = (sn    or '').strip()
        model = (model or 'Android Brevent Client').strip()

        if not key or not sn:
            return {'code': 400, 'body': {'status': 'ERROR', 'code': 'INVALID_PARAMS',
                                          'message': 'Parameter key dan sn wajib disertakan'}}
        db = load_db()
        k  = next((x for x in db.get('keys',[]) if x['key'].upper() == key), None)
        if not k:
            return {'code': 404, 'body': {'status': 'ERROR', 'code': 'KEY_NOT_FOUND',
                                          'message': 'Kunci lisensi tidak ditemukan'}}

        if k['status'] == 'banned':
            add_log(k, 'banned', f'Akses ditolak: Key BANNED (SN: {sn})')
            save_db(db)
            return {'code': 403, 'body': {'status': 'OFF', 'code': 'KEY_OFF',
                                          'message': 'Kunci dinonaktifkan oleh admin'}}

        if is_expired(k.get('expiresAt')):
            add_log(k, 'denied', f'Akses ditolak: Key EXPIRED (SN: {sn})')
            save_db(db)
            return {'code': 403, 'body': {'status': 'EXPIRED', 'code': 'KEY_EXPIRED',
                                          'message': 'Kunci lisensi telah kadaluarsa'}}

        ban = next((b for b in k.get('bannedDevices',[]) if b['serial'] == sn), None)
        if ban:
            add_log(k, 'banned', f'Akses ditolak: Device {sn} diblokir')
            save_db(db)
            return {'code': 403, 'body': {'status': 'DEVICE_BANNED', 'code': 'DEVICE_BANNED',
                                          'message': 'Perangkat diblokir pada kunci ini',
                                          'reason': ban.get('reason','-')}}

        devices = k.setdefault('devices', [])
        existing = next((d for d in devices if d['serial'] == sn), None)
        if not existing:
            max_dev = int(k.get('maxDevices', 0))
            if max_dev > 0 and len(devices) >= max_dev:
                add_log(k, 'denied', f'Kuota penuh ({max_dev} HP), SN: {sn}')
                save_db(db)
                return {'code': 403, 'body': {'status': 'QUOTA_FULL', 'code': 'QUOTA_EXCEEDED',
                                              'message': f'Batas maks {max_dev} HP tercapai'}}
            devices.append({'serial': sn, 'model': model, 'lastSeen': date_str()})
            add_log(k, 'ok', f'Brevent tersambung: {sn} ({model})')
        else:
            existing['model']    = model
            existing['lastSeen'] = date_str()

        save_db(db)
        return {'code': 200, 'body': {
            'status': 'SUCCESS', 'code': 'OK',
            'message': 'Akses Brevent disetujui',
            'key': k['key'], 'owner': k['name'],
            'expires_at': k.get('expiresAt') or 'Lifetime',
            'remaining': remaining(k.get('expiresAt')),
            'device_serial': sn, 'device_model': model,
            'total_devices': len(devices),
            'max_devices': k['maxDevices'] if k['maxDevices'] else 'Unlimited'
        }}

    # ── Script Generator (untuk buyer) ────────────────────────────
    def _gen_script(self, host, key):
        h = host.split(':')[0]
        p = host.split(':')[1] if ':' in host else '8080'
        return f"""#!/system/bin/sh
PKG="$1"
HOST="{h}"
PORT="{p}"
KEY="{key}"

MODEL=$(getprop ro.product.model 2>/dev/null | tr ' ' '_')
BRAND=$(getprop ro.product.brand 2>/dev/null)
SN=$(getprop ro.serialno 2>/dev/null)
[ -z "$SN" ] && SN=$(getprop ro.boot.serialno 2>/dev/null)
[ -z "$SN" ] && SN=$(settings get secure android_id 2>/dev/null)
[ -z "$SN" ] && SN="UNKNOWN"

echo "KTXSTABLE | $BRAND $MODEL | $SN"

RESP=$(printf "GET /api/brevent/check?key=%s&sn=%s&model=%s HTTP/1.0\\r\\nHost: %s\\r\\n\\r\\n" \\
    "$KEY" "$SN" "$MODEL" "$HOST" | nc -w 8 "$HOST" "$PORT" 2>/dev/null)

case "$RESP" in
    *SUCCESS*)
        echo "KEY VALID"
        ;;
    *OFF*)
        echo "KEY TIDAK VALID - Dibanned"
        am force-stop "$PKG" 2>/dev/null
        pm disable-user --user 0 "$PKG" 2>/dev/null
        am kill "$PKG" 2>/dev/null
        ;;
    *EXPIRED*)
        echo "KEY TIDAK VALID - Kadaluarsa"
        am force-stop "$PKG" 2>/dev/null
        pm disable-user --user 0 "$PKG" 2>/dev/null
        ;;
    *DEVICE_BANNED*)
        echo "KEY TIDAK VALID - Device dicekal"
        am force-stop "$PKG" 2>/dev/null
        pm disable-user --user 0 "$PKG" 2>/dev/null
        am kill "$PKG" 2>/dev/null
        ;;
    *QUOTA_FULL*)
        echo "KEY TIDAK VALID - Kuota penuh"
        am force-stop "$PKG" 2>/dev/null
        ;;
    *)
        echo "Tidak bisa konek ke server"
        am force-stop "$PKG" 2>/dev/null
        ;;
esac
"""

    # ── Helpers ───────────────────────────────────────────────────
    def _body(self):
        l = int(self.headers.get('Content-Length', 0))
        if not l: return {}
        try: return json.loads(self.rfile.read(l))
        except: return {}

    def _json(self, code, data):
        b = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(b))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b)

    def _html(self, html):
        b = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(b))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a): pass

class Server(HTTPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 8080))
    s = Server(('0.0.0.0', PORT), Handler)
    print(f'KTXSTABLE-SERVER → http://0.0.0.0:{PORT}')
    s.serve_forever()