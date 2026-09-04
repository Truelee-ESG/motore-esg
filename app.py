import os
import json
import base64
import time
import threading
import queue
import webbrowser
import requests
import pandas as pd
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

# ==========================================
# 1. LOGICA DI RICERCA CARTELLE
# ==========================================
def trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita, q):
    nome_file_config = f"config_{nome_cliente}.json"
    percorso_root = percorso_root.strip('"\'').strip()
    
    q.put(f"Avvio ricerca cartelle in corso dentro: {percorso_root}")
    
    config = {"ee": None, "gas": None, "api_key": api_key_inserita, "root": percorso_root}
    
    if os.path.exists(nome_file_config):
        try:
            with open(nome_file_config, 'r') as f:
                config_salvata = json.load(f)
                if config_salvata.get("root") == percorso_root:
                    config = config_salvata
                    q.put(f"Configurazione esistente caricata da {nome_file_config}")
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
                    q.put(f"[Trovata] Cartella Energia Elettrica: {config['ee']}")
                elif "gas" in nome_dir or "metano" in nome_dir or "gpl" in nome_dir:
                    config["gas"] = os.path.join(root, directory)
                    q.put(f"[Trovata] Cartella Gas: {config['gas']}")

    with open(nome_file_config, 'w') as f:
        json.dump(config, f, indent=4)
        
    q.put("Ricerca cartelle completata.")
    return config, f"Configurazione salvata in {nome_file_config}"

# ==========================================
# 2. SELEZIONE MODELLO OTTIMIZZATA
# ==========================================
def trova_modello_valido(api_key, q):
    q.put("Connessione a Google AI Studio per trovare il modello attivo...")
    modelli = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-3.6-flash', 'gemini-3.7-flash']
    
    for m in modelli:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": "test"}]}]}
        try:
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=5)
            if res.status_code != 404:
                q.put(f"-> Modello ottimale agganciato con successo: {m}")
                return m
        except Exception:
            continue
            
    q.put("-> Uso modello predefinito di riserva: gemini-2.5-flash")
    return 'gemini-2.5-flash'

# ==========================================
# 3. ESTRAZIONE SEQUENZIALE SICURA CON GESTIONE QUOTA 429
# ==========================================
def estrai_dati_da_pdf(percorso_file, tipo_bolletta, api_key, modello_iniziale, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Analisi bolletta: {nome_file}")
    
    with open(percorso_file, "rb") as doc_file:
        pdf_bytes = doc_file.read()
    pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
    
    prompt = f"""
    Leggi questa bolletta di {tipo_bolletta}.
    Estrai i dati e rispondi SOLO con un oggetto JSON valido con questa struttura:
    {{"mese": "gennaio", "anno": 2026, "consumo": 120.5, "unita_misura": "sm3", "tipo_gas": "metano"}}
    Se l'unità di misura è kWh, inserisci "kWh". Se è energia elettrica, tipo_gas deve essere vuoto "".
    """
    
    modelli_da_provare = [modello_iniziale] + [m for m in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-3.6-flash', 'gemini-3.7-flash'] if m != modello_iniziale]
    
    ultimo_errore = None
    
    for modello in modelli_da_provare:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modello}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": pdf_base64
                            }
                        },
                        {
                            "text": prompt
                        }
                    ]
                }
            ]
        }
        headers = {"Content-Type": "application/json"}
        
        tentativi = 4
        for tentativo in range(tentativi):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=60)
                if response.status_code == 200:
                    data_risposta = response.json()
                    testo = data_risposta['candidates'][0]['content']['parts'][0]['text']
                    testo_pulito = testo.strip().replace('```json', '').replace('```', '').strip()
                    dati = json.loads(testo_pulito)
                    
                    # Conversione kWh
                    consumo = float(dati.get('consumo', 0))
                    unita = str(dati.get('unita_misura', '')).lower()
                    tipo_gas = str(dati.get('tipo_gas', '')).lower()
                    
                    if unita == 'kwh':
                        kwh = consumo
                    else:
                        fattore = 1.0
                        if 'metano' in tipo_gas and unita in ['sm3', 'm3']:
                            fattore = 10.5  
                        elif 'gpl' in tipo_gas and unita in ['litri', 'l']:
                            fattore = 7.0
                        kwh = round(consumo * fattore, 2)
                        
                    dati['consumo_kwh_convertito'] = kwh
                    q.put(f"   [OK] {nome_file} -> {kwh} kWh")
                    return dati
                    
                elif response.status_code == 429:
                    # Raggiunto il limite di richieste di Google: leggiamo il tempo di attesa o usiamo 25 secondi di pausa
                    q.put(f"   [Quota Superata] Google richiede una pausa. Attendo 25 secondi prima di riprovare...")
                    time.sleep(25)
                    ultimo_errore = response.text
                    continue
                elif response.status_code == 503:
                    q.put(f"   [Server Occupati] Riprovo tra 5 secondi...")
                    time.sleep(5)
                    ultimo_errore = response.text
                    continue
                else:
                    ultimo_errore = response.text
                    break # Passa al modello successivo se l'errore è diverso
            except Exception as e:
                ultimo_errore = str(e)
                time.sleep(3)
                continue
                
    raise Exception(f"Impossibile elaborare il file {nome_file}. Ultimo errore: {ultimo_errore}")

def converti_in_kwh(dati):
    return dati.get('consumo_kwh_convertito', 0)

# ==========================================
# 4. INTERFACCIA WEB (FRONTEND)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Setup Iniziale Estrazione Consumi</title>
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
            
            <button type="submit">Avvia Ricerca ed Estrazione</button>
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
                q.put(f"ERRORE: La cartella specificata non esiste: {percorso_root}")
                q.put(("DONE", None))
                return

            config, msg_ricerca = trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita, q)
            chiave_attiva = config.get('api_key')
            
            if not chiave_attiva:
                q.put("ERRORE: API Key mancante o non valida.")
                q.put(("DONE", None))
                return

            modello_attivo = trova_modello_valido(chiave_attiva, q)

            dati_ee = []
            dati_gas = []

            cartella_ee = config.get("ee")
            file_ee_paths = []
            if cartella_ee and os.path.exists(cartella_ee):
                for f in os.listdir(cartella_ee):
                    if f.lower().endswith('.pdf'):
                        file_ee_paths.append(os.path.join(cartella_ee, f))
                        
            cartella_gas = config.get("gas")
            file_gas_paths = []
            if cartella_gas and os.path.exists(cartella_gas):
                for f in os.listdir(cartella_gas):
                    if f.lower().endswith('.pdf'):
                        file_gas_paths.append(os.path.join(cartella_gas, f))

            if not file_ee_paths and not file_gas_paths:
                q.put("ATTENZIONE: Nessun file PDF trovato nelle cartelle.")
                q.put(("DONE", None))
                return

            q.put(f"--- Inizio elaborazione di {len(file_ee_paths)} file EE e {len(file_gas_paths)} file Gas ---")

            # Elaborazione sequenziale con pausa di sicurezza anti-sovraccarico (anti-429) tra un file e l'altro
            for p in file_ee_paths:
                res = estrai_dati_da_pdf(p, "energia elettrica", chiave_attiva, modello_attivo, q)
                if res:
                    dati_ee.append(res)
                time.sleep(3) # Pausa di cortesia per rispettare i limiti di Google

            for p in file_gas_paths:
                res = estrai_dati_da_pdf(p, "gas", chiave_attiva, modello_attivo, q)
                if res:
                    dati_gas.append(res)
                time.sleep(3) # Pausa di cortesia per rispettare i limiti di Google

            q.put("--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_Consumi_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_ee:
                    pd.DataFrame(dati_ee).to_excel(writer, sheet_name='Energia_Elettrica', index=False)
                if dati_gas:
                    pd.DataFrame(dati_gas).to_excel(writer, sheet_name='Gas', index=False)
                if not dati_ee and not dati_gas:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel salvato in: {nome_file_excel}")
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
                .box {{ background: white; padding: 25px; border-radius: 8px; max-width: 750px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }}
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 350px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #1a73e8; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
                .btn:hover {{ background: #1557b0; }}
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
                <h2>Elaborazione Consumi ESG in Corso</h2>
                <p>Segui l'avanzamento delle operazioni in tempo reale:</p>
                <div id="log-box">Inizializzazione motore sicuro...</div>
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
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #2e7d32; text-align:center;\">Processo Completato con Successo!</h3><p style=\"text-align:center;\">File Excel salvato direttamente sul tuo Desktop:<br><b>{file_path}</b></p><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                else:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #c62828; text-align:center;\">Processo terminato con errori.</h3><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                break
            else:
                safe_msg = str(item).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                yield f"<script>appendLog('{safe_msg}');</script>\n"

    return Response(generate(), mimetype='text/html')

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)
