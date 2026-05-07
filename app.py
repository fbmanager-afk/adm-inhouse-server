"""
ADM In-House Processing Server v3 — Claude API Vision
Lee PDFs escaneados perfectamente usando claude-3-haiku.
Costo estimado: ~$0.03 por procesamiento.
"""
import os, json, re, base64, urllib.request, urllib.error, tempfile
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER   = os.environ.get("GITHUB_USER", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "adm-intel")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PORT          = int(os.environ.get("PORT", 8080))

# ── Extraer texto del PDF con Claude Vision ───────────────────
def extract_with_claude(pdf_bytes):
    """Manda el PDF a Claude y extrae el texto completo del In-House."""
    print(f"  Enviando PDF a Claude API ({len(pdf_bytes)//1024}KB)...")
    
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64
                    }
                },
                {
                    "type": "text",
                    "text": """Extrae TODA la información de este In-House Report del Hotel Arenas del Mar.
                    
Devuelve SOLO un JSON válido con esta estructura exacta:
{
  "fecha": "DD/MM/YYYY",
  "ocupadas": número,
  "total_hab": número,
  "adultos": número,
  "ninos": número,
  "total_pax": número,
  "pct_ocupacion": número,
  "mod": "nombre",
  "ayb": "nombre",
  "concierge": "nombre",
  "actividades": ["actividad1", "actividad2"],
  "guests": [
    {
      "hab": "101A",
      "nombre": "Apellido Nombre",
      "pax": 2,
      "entrada": "DD/MM",
      "salida": "DD/MM",
      "checkin_hoy": true/false,
      "checkout_hoy": true/false,
      "observaciones": "texto completo de observaciones",
      "alergias": ["descripcion alergia 1"],
      "cortesia": true/false,
      "vip": true/false,
      "lco": "hora o null"
    }
  ]
}

IMPORTANTE:
- checkin_hoy = true si la habitación tiene * o si la fecha de entrada es hoy
- checkout_hoy = true si la fecha de salida es hoy
- Incluye TODAS las habitaciones, incluyendo las que tienen *Reserva
- alergias: extrae TODAS las menciones de alergias, restricciones dietéticas
- observaciones: copia el texto completo tal como aparece
- Solo JSON, sin texto adicional"""
                }
            ]
        }]
    }

    req_data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=req_data,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())

    raw = resp["content"][0]["text"].strip()
    # Limpiar backticks si los hay
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    
    print(f"  Claude respondió: {len(raw)} chars")
    return json.loads(raw)

# ── Clasificar segmento ───────────────────────────────────────
def classify_segment(obs, alergias):
    ol = (obs + ' '.join(alergias)).lower()
    if any(k in ol for k in ['15x2','luna de miel','honeymoon','aniversario']): return 'luna'
    if any(k in ol for k in ['hijos','niños','nina','cuna','sofa cama grande']): return 'familia'
    if any(k in ol for k in ['vegetarian','celiac','gluten free','wellness']): return 'wellness'
    if any(k in ol for k in ['shareholders','vip agencia','memorable','aficionado']): return 'vip'
    if any(k in ol for k in ['europeos','audley']): return 'ecoluxe'
    if any(k in ol for k in ['rafting','canopy','aventura']): return 'adrenaline'
    if any(k in ol for k in ['bleisure','conference','empresa']): return 'bleisure'
    return 'nomad'

# ── Construir guest objects para el dashboard ─────────────────
def build_guests(raw_guests, fecha_hoy):
    guests = []
    for g in raw_guests:
        obs = g.get('observaciones','')
        alergias = g.get('alergias',[])
        ol = obs.lower()
        seg = classify_segment(obs, alergias)

        # Alerts
        alerts = []
        if g.get('checkout_hoy'): alerts.append('🧳 CHECKOUT HOY')
        if g.get('checkin_hoy'):  alerts.append('🔑 CHECK-IN HOY')
        if '15x2' in ol or 'luna de miel' in ol: alerts.append('🌹 LUNA DE MIEL')
        if 'aniversario' in ol: alerts.append('💍 ANIVERSARIO')
        if 'graduaci' in ol: alerts.append('🎓 GRADUACIÓN')
        if g.get('cortesia'): alerts.append('✨ CORTESÍA')
        if 'factura' in ol: alerts.append('🚨 FACTURA — REVISAR')
        if g.get('vip'): alerts.append('⭐ VIP')
        if any(k in ol for k in ['ato inter','87s','transfer']): alerts.append('🚐 TRANSFER')
        for a in alergias[:2]:
            if any(k in a.lower() for k in ['alérg','celiac','crítico']):
                alerts.append(f'⚠ {a[:50]}')

        guests.append({
            'h': g.get('hab','').replace('*','★'),
            'n': g.get('nombre','').title(),
            'pax': int(g.get('pax',2)),
            'in': g.get('entrada',''),
            'out': g.get('salida',''),
            'seg': seg,
            'checkout': bool(g.get('checkout_hoy',False)),
            'checkin':  bool(g.get('checkin_hoy',False)),
            'cortesia': bool(g.get('cortesia',False)),
            'vip': bool(g.get('vip',False)) or seg=='vip',
            'lco': g.get('lco'),
            'dietary': alergias[:5],
            'alerts': alerts[:6],
            'obs': obs[:200]
        })
    return guests

# ── Inyectar datos en el HTML ─────────────────────────────────
def inject_into_html(html, data, guests):
    fecha = data.get('fecha', datetime.now().strftime('%d/%m/%Y'))
    
    # Date
    html = re.sub(r"date:'[^']*'", f"date:'{fecha}'", html, count=1)
    
    # Stats
    html = re.sub(r"occ:\s*[\d.]+,", f"occ:{data.get('pct_ocupacion',0)},", html, count=1)
    html = re.sub(r"ocupadas:\s*\d+,", f"ocupadas:{data.get('ocupadas',0)},", html, count=1)
    html = re.sub(r"adultos:\s*\d+,", f"adultos:{data.get('adultos',0)},", html, count=1)
    html = re.sub(r"ninos:\s*\d+,", f"ninos:{data.get('ninos',0)},", html, count=1)
    html = re.sub(r"total_pax:\s*\d+,", f"total_pax:{data.get('total_pax',0)},", html, count=1)
    
    # Staff
    html = re.sub(r"MOD:'[^']*'", f"MOD:'{data.get('mod','')}'", html, count=1)
    html = re.sub(r"AyB:'[^']*'", f"AyB:'{data.get('ayb','')}'", html, count=1)
    html = re.sub(r"Concierge:'[^']*'", f"Concierge:'{data.get('concierge','')}'", html, count=1)
    
    # Actividades
    acts = json.dumps(data.get('actividades',[]), ensure_ascii=False)
    html = re.sub(r"actividades:\[[^\]]*\]", f"actividades:{acts}", html, count=1)
    
    # Guests array
    guests_json = json.dumps(guests, ensure_ascii=False)
    html = re.sub(r'(guests:\s*)\[.*?\](\s*\n\};)', 
                  lambda m: m.group(1) + guests_json + m.group(2),
                  html, count=1, flags=re.DOTALL)
    
    # Page title
    html = re.sub(r'In-House HOY · \d{2}/\d{2}/\d{4}', 
                  f'In-House HOY · {fecha}', html)
    html = re.sub(r'\d+ habitaciones · \d+ pax · [\d.]+% ocupación',
                  f"{data.get('ocupadas',0)} habitaciones · {data.get('total_pax',0)} pax · {data.get('pct_ocupacion',0)}% ocupación",
                  html, count=1)
    
    # Timestamp forzado
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    html = re.sub(r'<!-- (adm|ts)[^>]*-->', f'<!-- ts:{ts} -->', html, count=1)
    if f'<!-- ts:{ts} -->' not in html:
        html = html.replace('</head>', f'<!-- ts:{ts} --></head>', 1)
    
    return html

# ── GitHub deploy ─────────────────────────────────────────────
def github_deploy(html_content, token, user, repo):
    BASE = f"https://api.github.com/repos/{user}/{repo}"
    H = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "ADM/1.0"
    }
    b64 = base64.b64encode(html_content.encode()).decode()
    sha = None
    try:
        req = urllib.request.Request(f"{BASE}/contents/index.html", headers=H)
        with urllib.request.urlopen(req, timeout=15) as r:
            sha = json.loads(r.read()).get("sha")
    except: pass
    
    fecha = datetime.now().strftime('%d/%m %H:%M')
    p = {"message": f"ADM In-House {fecha}", "content": b64, "branch": "main"}
    if sha: p["sha"] = sha
    
    req = urllib.request.Request(
        f"{BASE}/contents/index.html",
        data=json.dumps(p).encode(), headers=H, method="PUT"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("commit",{}).get("sha","")[:8]

# ── HTTP Handler ──────────────────────────────────────────────
class ADMHandler(BaseHTTPRequestHandler):
    def log_message(self, f, *a):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {f%a}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200); self.send_cors(); self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors(); self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "version": "3-claude-api",
                "claude": bool(ANTHROPIC_KEY),
                "github": bool(GITHUB_TOKEN)
            }).encode())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/upload":
            self.send_response(404); self.end_headers(); return
        try:
            ct = self.headers.get("Content-Type", "")
            cl = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(cl)

            # Extraer PDF del multipart
            pdf = None
            if "multipart" in ct:
                bd = ct.split("boundary=")[-1].strip().encode()
                for part in body.split(b"--" + bd):
                    if b"filename" in part:
                        idx = part.find(b"\r\n\r\n")
                        if idx != -1:
                            pdf = part[idx+4:].rstrip(b"\r\n--")
                            break
            else:
                pdf = body

            if not pdf or len(pdf) < 100:
                raise ValueError("PDF inválido o vacío")

            print(f"  PDF recibido: {len(pdf)//1024}KB")

            # 1. Claude lee el PDF
            if not ANTHROPIC_KEY:
                raise ValueError("ANTHROPIC_API_KEY no configurada en el servidor")
            
            raw_data = extract_with_claude(pdf)
            print(f"  Datos extraídos: {len(raw_data.get('guests',[]))} huéspedes")

            # 2. Construir guests
            fecha_hoy = datetime.now().strftime("%d/%m")
            guests = build_guests(raw_data.get('guests',[]), fecha_hoy)

            # 3. Obtener HTML actual de GitHub
            BASE2 = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
            H2 = {"Authorization": f"Bearer {GITHUB_TOKEN}",
                  "Accept": "application/vnd.github+json",
                  "User-Agent": "ADM/1.0"}
            req = urllib.request.Request(f"{BASE2}/contents/index.html", headers=H2)
            with urllib.request.urlopen(req, timeout=15) as r:
                fd = json.loads(r.read())
            html = base64.b64decode(fd["content"]).decode("utf-8", errors="replace")

            # 4. Inyectar datos
            html_updated = inject_into_html(html, raw_data, guests)

            # 5. Publicar en GitHub
            commit = github_deploy(html_updated, GITHUB_TOKEN, GITHUB_USER, GITHUB_REPO)

            n_diet = sum(1 for g in guests if g['dietary'])
            resp = {
                "success": True,
                "fecha": raw_data.get('fecha',''),
                "guests": len(guests),
                "dietary": n_diet,
                "checkins": sum(1 for g in guests if g['checkin']),
                "checkouts": sum(1 for g in guests if g['checkout']),
                "occ": raw_data.get('pct_ocupacion', 0),
                "commit": commit,
                "url": f"https://{GITHUB_REPO}.netlify.app",
                "message": f"✅ {raw_data.get('fecha','')} · {len(guests)} huéspedes · {n_diet} alertas dietéticas · actualizando en ~20s"
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors(); self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

        except Exception as e:
            print(f"  ERROR: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_cors(); self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())

if __name__ == "__main__":
    print(f"🚀 ADM Server v3 · Claude API · Puerto {PORT}")
    print(f"   Claude API: {'✓' if ANTHROPIC_KEY else '✗ NO CONFIGURADA'}")
    print(f"   GitHub: {'✓' if GITHUB_TOKEN else '✗ NO CONFIGURADO'}")
    HTTPServer(("0.0.0.0", PORT), ADMHandler).serve_forever()
