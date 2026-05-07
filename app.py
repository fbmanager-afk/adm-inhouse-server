import os,json,re,base64,urllib.request,urllib.error,tempfile
from datetime import datetime
from http.server import HTTPServer,BaseHTTPRequestHandler

GITHUB_TOKEN=os.environ.get("GITHUB_TOKEN","")
GITHUB_USER=os.environ.get("GITHUB_USER","")
GITHUB_REPO=os.environ.get("GITHUB_REPO","adm-intel")
PORT=int(os.environ.get("PORT",8080))

def extract_pdf_text(pdf_bytes):
    text=""
    try:
        import pdfplumber
        with tempfile.NamedTemporaryFile(suffix=".pdf",delete=False) as f:
            f.write(pdf_bytes);fname=f.name
        with pdfplumber.open(fname) as pdf:
            for page in pdf.pages:
                t=page.extract_text()
                if t:text+=t+"\n"
        os.unlink(fname)
        if len(text.strip())>100:
            print(f"pdfplumber: {len(text)} chars");return text
    except Exception as e:
        print(f"pdfplumber fallo: {e}")
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        print("Intentando OCR...")
        images=convert_from_bytes(pdf_bytes,dpi=300)
        for img in images:
            t=pytesseract.image_to_string(img,lang="spa+eng")
            if t:text+=t+"\n"
        if len(text.strip())>100:
            print(f"OCR: {len(text)} chars");return text
    except Exception as e:
        print(f"OCR fallo: {e}")
    try:
        from pdfminer.high_level import extract_text as pm
        with tempfile.NamedTemporaryFile(suffix=".pdf",delete=False) as f:
            f.write(pdf_bytes);fname=f.name
        text=pm(fname);os.unlink(fname)
        if len(text.strip())>100:
            print(f"pdfminer: {len(text)} chars");return text
    except Exception as e:
        print(f"pdfminer fallo: {e}")
    return text

def parse_inhouse(text):
    guests=[]
    today=datetime.now().strftime("%d/%m")
    fecha=datetime.now().strftime("%d/%m/%Y")
    m=re.search(r"Fecha[:\s]+(\d{2}/\d{2}/\d{2,4})",text,re.I)
    if m:fecha=m.group(1)
    occ={"ocupadas":0,"total":37,"adultos":0,"ninos":0,"pax":0,"pct":0}
    for line in text.split("\n"):
        for k,fk in[("Ocupadas","ocupadas"),("Total Habit","total"),("Adultos","adultos")]:
            m2=re.search(rf"{k}[:\s]+(\d+)",line,re.I)
            if m2:occ[fk]=int(m2.group(1))
        m2=re.search(r"(\d+\.?\d*)\s*%",line)
        if m2 and float(m2.group(1))>50:occ["pct"]=float(m2.group(1))
        m2=re.search(r"Total[:\s]+(\d+)",line,re.I)
        if m2 and int(m2.group(1))>10:occ["pax"]=int(m2.group(1))
    staff={}
    for line in text.split("\n"):
        for key,var in[("MOD","MOD"),("Concierge","Concierge"),("A&B","AyB"),("Mantenimiento","Mant"),("Botones","Botones")]:
            if key in line:
                parts=line.split(key,1)
                if len(parts)>1:
                    val=parts[1].strip().lstrip(":").strip()[:40]
                    if val:staff[var]=val
    actividades=[]
    for line in text.split("\n"):
        if any(k in line.lower() for k in["lesson","dinner","bbq","tour","yoga"]):
            c=line.strip()
            if 5<len(c)<100:actividades.append(c)
    hab_re=re.compile(r"^(\d{3}[AB][\*]?)\s+([A-ZÁÉÍÓÚÑ][^\n]{3,40}?)\s+(\d)\s+(\d{2}/\d{2})\s+(\d{2}/\d{2})\s*(.*)?$",re.M|re.I)
    for m in hab_re.finditer(text):
        hab,nom,pax,entry,sal,obs=m.groups()
        obs=(obs or "").strip();ol=obs.lower()
        seg="nomad"
        if any(k in ol for k in["15x2","luna de miel","aniversario"]):seg="luna"
        elif any(k in ol for k in["hijos","niños","cuna"]):seg="familia"
        elif any(k in ol for k in["vegetarian","celiac","gluten free"]):seg="wellness"
        elif any(k in ol for k in["shareholders","vip","aficionado"]):seg="vip"
        elif any(k in ol for k in["europeos","audley"]):seg="ecoluxe"
        elif any(k in ol for k in["rafting","canopy"]):seg="adrenaline"
        dietary=[]
        for key,lbl in[("camar","ALÉRGICO CAMARÓN"),("pescado","ALÉRGICO PESCADO"),("celiac","CELIACA"),("gluten free","Sin gluten"),("vegetarian","Vegetariana"),("pescatarian","Pescatariana"),("marisco","ALÉRGICA MARISCOS"),("canela","ALÉRGICA canela/fresas/chocolate")]:
            if key in ol and lbl not in dietary:dietary.append(lbl)
        alerts=[]
        if sal==today:alerts.append("CHECKOUT HOY")
        if "*" in hab or entry==today:alerts.append("CHECK-IN HOY")
        if "luna de miel" in ol or "15x2" in ol:alerts.append("LUNA DE MIEL")
        if "aniversario" in ol:alerts.append("ANIVERSARIO")
        if "factura" in ol:alerts.append("FACTURA REVISAR")
        guests.append({"h":hab.replace("*",""),"n":nom.strip().title(),"pax":int(pax),"in":entry,"out":sal,"seg":seg,"checkout":sal==today,"checkin":"*" in hab or entry==today,"cortesia":"cortesia" in ol,"vip":seg=="vip","lco":None,"dietary":dietary,"alerts":alerts[:5],"obs":obs[:150]})
    return{"fecha":fecha,"occ":occ,"staff":staff,"actividades":actividades[:4],"guests":guests}

def inject(html,data):
    html=re.sub(r"const IH_DATE\s*=\s*'[^']*'",f"const IH_DATE='{data['fecha']}'",html)
    g=json.dumps(data["guests"],ensure_ascii=False)
    s=json.dumps(data["occ"],ensure_ascii=False)
    html=re.sub(r"const IH_STATS\s*=\s*\{[^}]*\}",f"const IH_STATS={s}",html)
    html=re.sub(r"(guests:\s*)\[[^\]]{10,}\]",rf"\g<1>{g}",html,flags=re.DOTALL)
    html=re.sub(r"In-House HOY · \d{2}/\d{2}/\d{4}",f"In-House HOY · {data['fecha']}",html)
    return html

def gh_deploy(html,token,user,repo):
    BASE=f"https://api.github.com/repos/{user}/{repo}"
    H={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json","User-Agent":"ADM/1.0"}
    b64=base64.b64encode(html.encode()).decode()
    sha=None
    try:
        req=urllib.request.Request(f"{BASE}/contents/index.html",headers=H)
        with urllib.request.urlopen(req,timeout=15) as r:sha=json.loads(r.read()).get("sha")
    except:pass
    p={"message":f"ADM {datetime.now().strftime('%d/%m %H:%M')}","content":b64,"branch":"main"}
    if sha:p["sha"]=sha
    req=urllib.request.Request(f"{BASE}/contents/index.html",data=json.dumps(p).encode(),headers=H,method="PUT")
    with urllib.request.urlopen(req,timeout=60) as r:return json.loads(r.read()).get("commit",{}).get("sha","")[:8]

class H(BaseHTTPRequestHandler):
    def log_message(self,f,*a):print(f"[{datetime.now().strftime('%H:%M:%S')}] {f%a}")
    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","POST,GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
    def do_OPTIONS(self):self.send_response(200);self.send_cors();self.end_headers()
    def do_GET(self):
        if self.path=="/health":
            self.send_response(200);self.send_header("Content-Type","application/json");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps({"status":"ok","version":"2-ocr"}).encode())
        else:self.send_response(404);self.end_headers()
    def do_POST(self):
        if self.path!="/upload":self.send_response(404);self.end_headers();return
        try:
            ct=self.headers.get("Content-Type","")
            cl=int(self.headers.get("Content-Length",0))
            body=self.rfile.read(cl)
            pdf=None
            if "multipart" in ct:
                bd=ct.split("boundary=")[-1].strip().encode()
                for part in body.split(b"--"+bd):
                    if b"filename" in part:
                        idx=part.find(b"\r\n\r\n")
                        if idx!=-1:pdf=part[idx+4:].rstrip(b"\r\n--");break
            else:pdf=body
            if not pdf or len(pdf)<100:raise ValueError("PDF invalido")
            print(f"PDF recibido: {len(pdf)} bytes")
            text=extract_pdf_text(pdf)
            if not text.strip():raise ValueError("No se pudo extraer texto del PDF")
            data=parse_inhouse(text)
            print(f"Huespedes: {len(data['guests'])}")
            BASE2=f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
            H2={"Authorization":f"Bearer {GITHUB_TOKEN}","Accept":"application/vnd.github+json","User-Agent":"ADM/1.0"}
            req=urllib.request.Request(f"{BASE2}/contents/index.html",headers=H2)
            with urllib.request.urlopen(req,timeout=15) as r:fd=json.loads(r.read())
            html=base64.b64decode(fd["content"]).decode("utf-8",errors="replace")
            html2=inject(html,data)
            sha=gh_deploy(html2,GITHUB_TOKEN,GITHUB_USER,GITHUB_REPO)
            resp={"success":True,"fecha":data["fecha"],"guests":len(data["guests"]),"dietary":sum(1 for g in data["guests"] if g["dietary"]),"checkins":sum(1 for g in data["guests"] if g["checkin"]),"checkouts":sum(1 for g in data["guests"] if g["checkout"]),"occ":data["occ"].get("pct",0),"commit":sha,"url":f"https://{GITHUB_REPO}.netlify.app","message":f"OK {data['fecha']} · {len(data['guests'])} huespedes"}
            self.send_response(200);self.send_header("Content-Type","application/json;charset=utf-8");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps(resp,ensure_ascii=False).encode())
        except Exception as e:
            print(f"ERROR: {e}")
            self.send_response(500);self.send_header("Content-Type","application/json");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps({"success":False,"error":str(e)}).encode())

if __name__=="__main__":
    print(f"ADM Server v2 OCR · Puerto {PORT}")
    HTTPServer(("0.0.0.0",PORT),H).serve_forever()
