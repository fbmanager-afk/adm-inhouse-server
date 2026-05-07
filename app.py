"""
ADM In-House Processing Server
Recibe PDF del In-House, extrae datos, actualiza GitHub → Netlify publica solo.
Deploy en Render.com — gratis, sin configuración de servidor.
"""
import os, json, re, base64, hashlib, urllib.request, urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import cgi, tempfile, threading

# ── Configuración ─────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER  = os.environ.get("GITHUB_USER", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "adm-intel")
PORT         = int(os.environ.get("PORT", 8080))

# ── Parser del In-House ADM ───────────────────────────────────────────────────
def parse_inhouse(text):
    """Extrae datos estructurados del texto del In-House de ADM."""
    guests = []
    lines = text.split('\n')

    # Detectar fecha
    fecha = datetime.now().strftime("%d/%m/%Y")
    for line in lines:
        m = re.search(r'(\d{2}/\d{2}/\d{2,4})', line)
        if m and 'Fecha' in line:
            fecha = m.group(1)
            break

    # Detectar ocupación
    occ_data = {"ocupadas": 0, "total": 37, "adultos": 0, "ninos": 0, "pax": 0, "pct": 0}
    for line in lines:
        m = re.search(r'Ocupadas[:\s]+(\d+)', line, re.I)
        if m: occ_data["ocupadas"] = int(m.group(1))
        m = re.search(r'Total Habit[:\s]+(\d+)', line, re.I)
        if m: occ_data["total"] = int(m.group(1))
        m = re.search(r'Adultos[:\s]+(\d+)', line, re.I)
        if m: occ_data["adultos"] = int(m.group(1))
        m = re.search(r'Ni.os[:\s]+(\d+)', line, re.I)
        if m: occ_data["ninos"] = int(m.group(1))
        m = re.search(r'Total[:\s]+(\d+)', line, re.I)
        if m: occ_data["pax"] = int(m.group(1))
        m = re.search(r'(\d+\.?\d*)\s*%', line)
        if m and float(m.group(1)) > 50:
            occ_data["pct"] = float(m.group(1))

    # Detectar staff
    staff = {}
    staff_keys = {'MOD':'MOD','Puesto 1':'Puesto1','Concierge':'Concierge',
                  'A&B':'AyB','Botones':'Botones','Mantenimiento':'Mant',
                  'Welcome':'Welcome','Areas':'Areas','Brigadista':'Brigadista'}
    for line in lines:
        for key, var in staff_keys.items():
            if key in line:
                parts = line.split(key, 1)
                if len(parts) > 1:
                    val = parts[1].strip().lstrip(':').strip()
                    if val: staff[var] = val[:40]

    # Detectar actividades
    actividades = []
    for line in lines:
        if any(k in line.lower() for k in ['lesson','dinner','tour','activity','yoga','bbq']):
            clean = line.strip()
            if 5 < len(clean) < 80:
                actividades.append(clean)

    # Detectar huéspedes — formato: HAB NOMBRE PAX ENTRADA SALIDA OBSERVACIONES
    hab_pattern = re.compile(
        r'^(\d{3}[AB][\*]?)\s+'           # habitación
        r'([A-ZÁÉÍÓÚÑ][^0-9\n]{3,35})\s+' # nombre
        r'(\d)\s+'                          # pax
        r'(\d{2}/\d{2})\s+'                # entrada
        r'(\d{2}/\d{2})\s*'                # salida
        r'(.*)?$',                          # observaciones
        re.MULTILINE | re.IGNORECASE
    )

    for m in hab_pattern.finditer(text):
        hab   = m.group(1).strip()
        nom   = m.group(2).strip().title()
        pax   = int(m.group(3))
        entry = m.group(4)
        sal   = m.group(5)
        obs   = (m.group(6) or "").strip()

        checkout = sal == datetime.now().strftime("%d/%m")
        checkin  = "*" in hab or entry == datetime.now().strftime("%d/%m")

        # Clasificar segmento
        obs_l = obs.lower()
        seg = "nomad"
        if any(k in obs_l for k in ["15x2","luna de miel","honeymoon","aniversario","anniversary"]):
            seg = "luna"
        elif any(k in obs_l for k in ["hijos","niños","cuna","sofa cama grande","familia"]):
            seg = "familia"
        elif any(k in obs_l for k in ["vegetarian","celiac","gluten free","wellness","yoga"]):
            seg = "wellness"
        elif any(k in obs_l for k in ["shareholders","aficionado","vip agencia"]):
            seg = "vip"
        elif any(k in obs_l for k in ["europeos","audley","ecoluxe","sostenib"]):
            seg = "ecoluxe"
        elif any(k in obs_l for k in ["rafting","canopy","aventura","adrenaline"]):
            seg = "adrenaline"
        elif any(k in obs_l for k in ["reunion","conference","bleisure","empresa"]):
            seg = "bleisure"

        # Extraer alertas
        alerts = []
        if checkout: alerts.append("🧳 CHECKOUT HOY")
        if checkin:  alerts.append("🔑 CHECK-IN HOY")
        if "luna de miel" in obs_l or "15x2" in obs_l: alerts.append("🌹 LUNA DE MIEL")
        if "aniversario" in obs_l: alerts.append("💍 ANIVERSARIO")
        if any(k in obs_l for k in ["cumpleaños","birthday"]): alerts.append("🎂 CUMPLEAÑOS")
        if any(k in obs_l for k in ["ato inter","transfer","91 a","87s","88 "]): alerts.append("🚐 TRANSFER")
        if "factura" in obs_l: alerts.append("🚨 FACTURA — REVISAR")

        # Extraer alergias
        dietary = []
        alergia_keys = [
            ("camarón","ALÉRGICO AL CAMARÓN — crítico"),
            ("camaron","ALÉRGICO AL CAMARÓN — crítico"),
            ("pescado","ALÉRGICO AL PESCADO — crítico"),
            ("mariscos","ALÉRGICA A MARISCOS — crítico"),
            ("gluten","Sin gluten / celiaca"),
            ("celiaca","CELIACA — sin gluten absoluto"),
            ("celiac","CELIACA — sin gluten absoluto"),
            ("vegetarian","Vegetariana"),
            ("pescatarian","Pescatariana"),
            ("canela","ALÉRGICA: canela/lavanda/fresas/chocolate"),
            ("lácteos","Sin lácteos"),
            ("lacteos","Sin lácteos"),
            ("soja","Sin soja"),
            ("cacahuetes","Sin cacahuetes"),
        ]
        seen = set()
        for key, label in alergia_keys:
            if key in obs_l and label not in seen:
                dietary.append(label)
                seen.add(label)
                if key in ["camarón","camaron","pescado","mariscos","canela","celiaca","celiac"]:
                    alerts.append(f"⚠ {label}")

        # Cortesía
        cortesia = any(k in obs_l for k in ["cortesía","cortesia","colocar cortes"])
        vip = any(k in obs_l for k in ["shareholders","vip","aficionado","cayuga afic"])
        lco = None
        m_lco = re.search(r'LT\s*(\d+\s*(?:md|pm|am)?)', obs, re.I)
        if m_lco: lco = m_lco.group(1)

        guests.append({
            "h": hab.replace("*","★"),
            "n": nom,
            "pax": pax,
            "in": entry,
            "out": sal,
            "seg": seg,
            "checkout": checkout,
            "checkin": checkin,
            "cortesia": cortesia,
            "vip": vip,
            "lco": lco,
            "dietary": dietary,
            "alerts": alerts[:6],
            "obs": obs[:200]
        })

    return {
        "fecha": fecha,
        "occ": occ_data,
        "staff": staff,
        "actividades": actividades[:4],
        "guests": guests
    }

# ── Inyectar datos en el HTML del dashboard ──────────────────────────────────
def inject_into_html(html, data):
    """Reemplaza los datos del IH en el HTML del dashboard."""
    guests_json = json.dumps(data["guests"], ensure_ascii=False)
    stats_json  = json.dumps(data["occ"], ensure_ascii=False)
    staff_json  = json.dumps(data["staff"], ensure_ascii=False)
    acts_json   = json.dumps(data["actividades"], ensure_ascii=False)
    fecha       = data["fecha"]

    # Reemplazar IH_DATE
    html = re.sub(r"const IH_DATE\s*=\s*'[^']*'", f"const IH_DATE='{fecha}'", html)
    # Reemplazar IH_STATS
    html = re.sub(r"const IH_STATS\s*=\s*\{[^}]*\}",
                  f"const IH_STATS={stats_json}", html)
    # Reemplazar IH_STAFF
    html = re.sub(r"const IH_STAFF\s*=\s*\{[^}]*\}",
                  f"const IH_STAFF={staff_json}", html)
    # Reemplazar IH.guests array completo
    html = re.sub(
        r"(const IH\s*=\s*\{[^}]*date:'[^']*',\s*occ:[^,]*,.*?guests:\s*)\[[^\]]*\]",
        rf"\g<1>{guests_json}",
        html, flags=re.DOTALL
    )
    # Reemplazar IH.actividades
    html = re.sub(
        r"(actividades:\s*)\[[^\]]*\]",
        rf"\g<1>{acts_json}",
        html, count=1
    )
    # Actualizar título de la página con la fecha
    html = re.sub(r"In-House HOY · \d{2}/\d{2}/\d{4}",
                  f"In-House HOY · {fecha}", html)

    return html

# ── GitHub deploy ─────────────────────────────────────────────────────────────
def github_deploy(html_content, token, user, repo):
    BASE = f"https://api.github.com/repos/{user}/{repo}"
    HEADERS = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type":  "application/json",
        "User-Agent":    "ADM-Server/1.0"
    }
    content_b64 = base64.b64encode(html_content.encode()).decode()

    # Obtener SHA actual
    sha = None
    try:
        req = urllib.request.Request(f"{BASE}/contents/index.html", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            sha = json.loads(r.read()).get("sha")
    except: pass

    payload = {
        "message": f"ADM In-House · {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "content": content_b64,
        "branch": "main"
    }
    if sha: payload["sha"] = sha

    req = urllib.request.Request(
        f"{BASE}/contents/index.html",
        data=json.dumps(payload).encode(),
        headers=HEADERS, method="PUT"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
    return result.get("commit", {}).get("sha", "")[:8]

# ── Extraer texto del PDF ─────────────────────────────────────────────────────
def extract_pdf_text(pdf_bytes):
    try:
        import pdfplumber
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            fname = f.name
        text = ""
        with pdfplumber.open(fname) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text += t + "\n"
        os.unlink(fname)
        return text
    except ImportError:
        # Fallback: pdfminer
        try:
            from pdfminer.high_level import extract_text as pm_extract
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                fname = f.name
            text = pm_extract(fname)
            os.unlink(fname)
            return text
        except:
            return ""

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class ADMHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "server": "ADM In-House Processor"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/upload":
            self.send_response(404)
            self.end_headers()
            return

        try:
            content_type = self.headers.get("Content-Type", "")
            content_len  = int(self.headers.get("Content-Length", 0))
            body         = self.rfile.read(content_len)

            # Extraer PDF del multipart
            pdf_bytes = None
            if "multipart/form-data" in content_type:
                boundary = content_type.split("boundary=")[-1].strip().encode()
                parts = body.split(b"--" + boundary)
                for part in parts:
                    if b"Content-Disposition" in part and b"filename" in part:
                        idx = part.find(b"\r\n\r\n")
                        if idx != -1:
                            pdf_bytes = part[idx+4:].rstrip(b"\r\n--")
                            break
            else:
                # Raw PDF body
                pdf_bytes = body

            if not pdf_bytes or len(pdf_bytes) < 100:
                raise ValueError("PDF vacío o inválido")

            # 1. Extraer texto
            pdf_text = extract_pdf_text(pdf_bytes)
            if not pdf_text.strip():
                raise ValueError("No se pudo extraer texto del PDF. ¿Es una imagen escaneada?")

            # 2. Parsear datos
            data = parse_inhouse(pdf_text)
            n_guests = len(data["guests"])
            n_diet   = sum(1 for g in data["guests"] if g["dietary"])

            # 3. Cargar HTML base del repo
            BASE = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
            HEADERS = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ADM-Server/1.0"
            }
            req = urllib.request.Request(f"{BASE}/contents/index.html", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                file_data = json.loads(r.read())
            html = base64.b64decode(file_data["content"]).decode("utf-8", errors="replace")

            # 4. Inyectar datos
            html_updated = inject_into_html(html, data)

            # 5. Subir a GitHub
            commit_sha = github_deploy(html_updated, GITHUB_TOKEN, GITHUB_USER, GITHUB_REPO)

            # Respuesta exitosa
            resp = {
                "success": True,
                "fecha": data["fecha"],
                "guests": n_guests,
                "dietary": n_diet,
                "checkins": sum(1 for g in data["guests"] if g["checkin"]),
                "checkouts": sum(1 for g in data["guests"] if g["checkout"]),
                "occ": data["occ"].get("pct", 0),
                "commit": commit_sha,
                "url": f"https://{GITHUB_REPO}.netlify.app",
                "message": f"✅ In-House {data['fecha']} procesado · {n_guests} huéspedes · actualizando en ~20s"
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors()
            self.end_headers()
            err = {"success": False, "error": str(e)}
            self.wfile.write(json.dumps(err).encode())

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 ADM In-House Server · Puerto {PORT}")
    print(f"   GitHub: {GITHUB_USER}/{GITHUB_REPO}")
    server = HTTPServer(("0.0.0.0", PORT), ADMHandler)
    server.serve_forever()
