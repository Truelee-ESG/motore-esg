import os
import json
import base64
import time
import threading
import queue
import webbrowser
import requests
import pandas as pd
from pypdf import PdfReader
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

# ==========================================
# 1. RICERCA CARTELLE
# ==========================================
def trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita, q):
    nome_file_config = f"config_{nome_cliente}.json"
    percorso_root = percorso_root.strip('"\'').strip()
    config = {"ee": None, "gas": None, "api_key": api_key_inserita, "root": percorso_root}
    
    if os.path.exists(nome_file_config):
        try:
            with open(nome_file_config, 'r') as f:
                config_salvata = json.load(f)
                if config_salvata.get("root") == percorso_root:
                    config = config_salvata
        except Exception:
            pass

    if api_key_inserita:
        config['api_key'] = api_key_inserita

    if not config.get("ee") or not config.get("gas") or not os.path.exists(str(config.get("ee", ""))):
        q.put("Scansione delle cartelle in corso...")
        for root, dirs, files in os.walk(percorso_root):
            for directory in dirs:
                nome_dir = directory.lower()
                if "energia" in nome_dir or "elettric" in nome_dir or "e.e" in nome_dir or "luce" in nome_dir:
                    config["ee"] = os.path.join(root, directory)
                    q.put(f"[Trovata] Cartella EE: {config['ee']}")
                elif "gas" in nome_dir or "metano" in nome_dir or "gpl" in nome_dir:
                    config["gas"] = os.path.join(root, directory)
                    q.put(f"[Trovata] Cartella Gas: {config['gas']}")

    with open(nome_file_config, 'w') as f:
        json.dump(config, f, indent=4)
        
    return config

# ==========================================
# 2. LETTURA LOCALE TESTO (ULTRARAPIDA)
# ==========================================
def leggi_testo_pdf_locale(percorso_file):
    testo = ""
    try:
        reader = PdfReader(percorso_file)
        for page in reader.pages:
            estratto = page.extract_text()
            if estratto:
                testo += estratto + "\n"
    except Exception:
        pass
    return testo

def calcola_kwh(item):
    try:
        consumo = float(item.get('consumo', 0))
    except (ValueError, TypeError):
        consumo = 0.0
    unita = str(item.get('unita_misura', '')).lower()
    tipo_gas = str(item.get('tipo_gas', '')).lower()
    if unita == 'kwh':
        return consumo
    fattore = 1.0
    if 'metano' in tipo_gas and unita in ['sm3', 'm3']:
        fattore = 10.5  
    elif 'gpl' in tipo_gas and unita in ['litri', 'l']:
        fattore = 7.0
    return round(consumo * fattore, 2)

# ==========================================
# 3. MOTORE IBRIDO: TESTO IN BLOCCO + VISIVO SINGOLO
# ==========================================
def estrai_dati_intelligente(file_paths, tipo_bolletta, api_key, q):
    if not file_paths:
        return []
        
    dati_finali = []
    bollette_testuali = []
    bollette_immagini = []

    q.put(f"\n--- FASE 1: Lettura ultrarapida dal tuo PC ({tipo_bolletta}) ---")
    for p in file_paths:
        nome_file = os.path.basename(p)
        testo = leggi_testo_pdf_locale(p)
        if len(testo.strip()) > 50:
            bollette_testuali.append({"nome_file": nome_file, "testo": testo})
            q.put(f"   [Testo Estratto] {nome_file}")
        else:
            bollette_immagini.append(p)
            q.put(f"   [Scansione Visiva Richiesta] {nome_file}")

    modello_attivo = 'gemini-1.5-flash' # Modello super-veloce perfetto per il testo

    # A) ELABORAZIONE IN BLOCCO DEI TESTI (Meno di 5 secondi per decine di file)
    if bollette_testuali:
        q.put(f"--- FASE 2: Invio cumulativo leggero di {len(bollette_testuali)} file a Gemini ---")
        prompt_testi = f"Analizza queste bollette di {tipo_bolletta}:\n\n"
        for tb in bollette_testuali:
            prompt_testi += f"--- INIZIO BOLLETTA {tb['nome_file']} ---\n{tb['testo']}\n--- FINE BOLLETTA {tb['nome_file']} ---\n\n"
            
        prompt_testi += """
        Estrai i dati per OGNI SINGOLA bolletta e restituisci UN UNICO ARRAY JSON valido (senza markdown) con questa struttura per ogni elemento:
        [
          {"nome_file": "nome_file.pdf", "mese": "gennaio", "anno": 2026, "consumo": 120.5, "unita_misura": "sm3", "tipo_gas": "metano"}
        ]
        Se l'unità di misura è kWh, inserisci "kWh". Se è energia elettrica, tipo_gas deve essere vuoto "".
        Rispondi ESCLUSIVAMENTE con l'array JSON.
        """
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modello_attivo}:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt_testi}]}]}
        
        for tentativo in range(3):
            try:
                res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
                if res.status_code == 200:
                    testo_risposta = res.json()['candidates'][0]['content']['parts'][0]['text']
                    testo_pulito = testo_risposta.strip().replace('```json', '').replace('```', '').strip()
                    try:
                        risultati_lista = json.loads(testo_pulito)
                        for item in risultati_lista:
                            item['consumo_kwh_convertito'] = calcola_kwh(item)
                            dati_finali.append(item)
                            q.put(f"   [OK] Dati estratti da {item.get('nome_file', 'file')} -> {item['consumo_kwh_convertito']} kWh")
                        break
                    except json.JSONDecodeError:
                        q.put("   [Avviso] Errore di formato da Gemini, riprovo...")
                        time.sleep(3)
                elif res.status_code in [503, 429]:
                    q.put("   [Server Occupato] Attendo 5 secondi...")
                    time.sleep(5)
                else:
                    q.put(f"   [Errore API] {res.text}")
                    break
            except Exception as e:
                q.put(f"   [Errore Rete] Riprovo... ({e})")
                time.sleep(3)

    # B) ELABORAZIONE DEI FILE IMMAGINE/SCANSIONATI (Gestiti uno alla volta in sicurezza)
    if bollette_immagini:
        q.put(f"--- FASE 3: Analisi IA Visiva per {len(bollette_immagini)} file scansionati ---")
        for p in bollette_immagini:
            nome_file = os.path.basename(p)
            q.put(f" -> Invio immagine a Gemini: {nome_file}")
            try:
                with open(p, "rb") as doc_file:
                    pdf_bytes = doc_file.read()
                pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
                
                prompt = f"""Leggi questa bolletta di {tipo_bolletta}. Estrai i dati in UN ARRAY JSON di 1 elemento:
                [{{"nome_file": "{nome_file}", "mese": "gennaio", "anno": 2026, "consumo": 120.5, "unita_misura": "sm3", "tipo_gas": "metano"}}]"""
                
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{modello_attivo}:generateContent?key={api_key}"
                payload = {"contents": [{"parts": [{"inline_data": {"mime_type": "application/pdf", "data": pdf_base64}}, {"text": prompt}]}]}
                
                # Un solo tentativo o passa al file successivo per sicurezza
                res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
                if res.status_code == 200:
                    testo_risposta = res.json()['candidates'][0]['content']['parts'][0]['text']
                    testo_pulito = testo_risposta.strip().replace('```json', '').replace('```', '').strip()
                    item = json.loads(testo_pulito)[0]
                    item['consumo_kwh_convertito'] = calcola_kwh(item)
                    dati_finali.append(item)
                    q.put(f"   [OK Visivo] {nome_file} -> {item['consumo_kwh_convertito']} kWh")
                else:
                    q.put(f"   [Errore Visivo] File saltato a causa del server occupato.")
                
                time.sleep(4) # Pausa obbligatoria per i file immagine per evitare il blocco 429
            except Exception as e:
                q.put(f"   [Errore Elaborazione] {e}")

    return dati_finali

# ==========================================
# 4. INTERFACCIA WEB (FRONTEND)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Motore ESG Ultraveloce</title>
    <style>
        body { font-family: Arial; padding: 40px; background: #f4f6f8; }
        .box { background: white; padding: 25px; border-radius: 8px; max-width: 500px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }
        input { width: 100%; padding: 10px; margin: 8px 0 16px 0; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 12px 20px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; width: 100%; font-weight: bold; }
        button:hover { background: #1557b0; }
        h2 { color: #1a73e8; text-align: center; margin-top: 0; }
        label { font-weight: bold; font-size: 0.9em; color: #333; }
    </style>
</head>
<body>
    <div class="box">
        <h2>Motore di Estrazione Consumi ESG</h2>
        <form action="/avvia" method="POST">
            <label>Nome Azienda/Cliente (senza spazi):</label>
            <input type="text" name="nome_cliente" placeholder="es. ditta_rossi" required>
            <label>Percorso Server/Cartella Principale (Root):</label>
            <input type="text" name="percorso_root" placeholder="es. C:\\Archivio_Dati" required>
            <label>Google AI Studio API Key (Password):</label>
            <input type="password" name="api_key" placeholder="Incolla qui la tua chiave API" required>
            <button type="submit">Avvia Motore Ultraveloce</button>
        </form>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_PAGE)

@app.route('/avvia', methods=['POST'])
def avvia_processo():
    nome_cliente = request.form['nome_cliente'].strip()
    percorso_root = request.form['percorso_root'].strip()
    api_key_inserita = request.form['api_key'].strip()
    q = queue.Queue()

    def background_worker():
        try:
            if not os.path.exists(percorso_root):
                q.put(f"ERRORE: La cartella non esiste: {percorso_root}")
                q.put(("DONE", None))
                return

            q.put("Inizializzazione motore Ibrido Ultraveloce...")
            config = trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita, q)
            chiave_attiva = config.get('api_key')

            file_ee_paths = []
            if config.get("ee") and os.path.exists(config["ee"]):
                file_ee_paths = [os.path.join(config["ee"], f) for f in os.listdir(config["ee"]) if f.lower().endswith('.pdf')]
                        
            file_gas_paths = []
            if config.get("gas") and os.path.exists(config["gas"]):
                file_gas_paths = [os.path.join(config["gas"], f) for f in os.listdir(config["gas"]) if f.lower().endswith('.pdf')]

            if not file_ee_paths and not file_gas_paths:
                q.put("ATTENZIONE: Nessun file PDF trovato.")
                q.put(("DONE", None))
                return

            q.put(f"Trovati {len(file_ee_paths)} file Energia Elettrica e {len(file_gas_paths)} file Gas.")

            # Elaborazione radicale ultrarapida
            dati_ee = estrai_dati_intelligente(file_ee_paths, "energia elettrica", chiave_attiva, q)
            dati_gas = estrai_dati_intelligente(file_gas_paths, "gas", chiave_attiva, q)

            q.put("\n--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_Consumi_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_ee: pd.DataFrame(dati_ee).to_excel(writer, sheet_name='Energia_Elettrica', index=False)
                if dati_gas: pd.DataFrame(dati_gas).to_excel(writer, sheet_name='Gas', index=False)
                if not dati_ee and not dati_gas:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto: {nome_file_excel}")
            q.put(("DONE", nome_file_excel))
        except Exception as err:
            import traceback
            q.put(f"ERRORE CRITICO:\n{traceback.format_exc()}")
            q.put(("DONE", None))

    threading.Thread(target=background_worker).start()

    def generate():
        yield f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Elaborazione Consumi ESG</title>
            <style>
                body {{ font-family: Arial; padding: 40px; background: #f4f6f8; }}
                .box {{ background: white; padding: 25px; border-radius: 8px; max-width: 800px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }}
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 400px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #1a73e8; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
            </style>
            <script>
                function appendLog(text) {{
                    const box = document.getElementById('log-box');
                    box.innerHTML += text + "\\n";
                    box.scrollTop = box.scrollHeight;
                }}
            </script>
        </head>
        <body>
            <div class="box">
                <h2>Elaborazione Consumi ESG Ultraveloce</h2>
                <div id="log-box"></div>
                <div id="result-area"></div>
            </div>
        </body>
        </html>
        """
        
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "DONE":
                file_path = item[1]
                if file_path:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #2e7d32; text-align:center;\">Processo Completato con Successo!</h3><p style=\"text-align:center;\">File salvato sul Desktop:<br><b>{file_path}</b></p><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                else:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #c62828; text-align:center;\">Terminato con errori.</h3><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                break
            else:
                safe_msg = str(item).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                yield f"<script>appendLog('{safe_msg}');</script>\n"

    return Response(generate(), mimetype='text/html')

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)
