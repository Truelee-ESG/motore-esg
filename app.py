import os
import json
import time
import threading
import webbrowser
import pandas as pd
from google import genai
from flask import Flask, request, render_template_string

app = Flask(__name__)

# ==========================================
# 1. LOGICA DI RICERCA CARTELLE
# ==========================================
def trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita):
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

    if not config.get("ee") or not config.get("gas") or not os.path.exists(str(config.get("ee"))):
        for root, dirs, files in os.walk(percorso_root):
            for directory in dirs:
                nome_dir = directory.lower()
                if "energia" in nome_dir or "elettric" in nome_dir or "e.e" in nome_dir or "luce" in nome_dir:
                    config["ee"] = os.path.join(root, directory)
                elif "gas" in nome_dir or "metano" in nome_dir or "gpl" in nome_dir:
                    config["gas"] = os.path.join(root, directory)

    with open(nome_file_config, 'w') as f:
        json.dump(config, f, indent=4)
        
    return config, f"Configurazione salvata in {nome_file_config}"

# ==========================================
# 2. LOGICA ESTRAZIONE E CONVERSIONE
# ==========================================
def estrai_dati_da_pdf(client, percorso_file, tipo_bolletta):
    file_caricato = client.files.upload(file=percorso_file)
    
    while file_caricato.state.name == 'PROCESSING':
        time.sleep(2)
        file_caricato = client.files.get(name=file_caricato.name)
        
    prompt = f"""
    Leggi questa bolletta di {tipo_bolletta}.
    Estrai i dati e rispondi SOLO con un oggetto JSON valido con questa struttura:
    {{"mese": "gennaio", "anno": 2026, "consumo": 120.5, "unita_misura": "sm3", "tipo_gas": "metano"}}
    Se l'unità di misura è kWh, inserisci "kWh". Se è energia elettrica, tipo_gas deve essere vuoto "".
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[file_caricato, prompt]
    )
    
    try:
        client.files.delete(name=file_caricato.name)
    except Exception:
        pass
        
    testo_pulito = response.text.strip().replace('```json', '').replace('```', '').strip()
    return json.loads(testo_pulito)

def converti_in_kwh(dati):
    try:
        consumo = float(dati.get('consumo', 0))
    except (ValueError, TypeError):
        consumo = 0.0
        
    unita = str(dati.get('unita_misura', '')).lower()
    tipo_gas = str(dati.get('tipo_gas', '')).lower()
    
    if unita == 'kwh':
        return consumo
        
    fattore = 1.0
    if 'metano' in tipo_gas and unita in ['sm3', 'm3']:
        fattore = 10.5  
    elif 'gpl' in tipo_gas and unita in ['litri', 'l']:
        fattore = 7.0
        
    return round(consumo * fattore, 2)

# ==========================================
# 3. INTERFACCIA WEB (FRONTEND)
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
    try:
        nome_cliente = request.form['nome_cliente'].strip()
        percorso_root = request.form['percorso_root'].strip()
        api_key_inserita = request.form['api_key'].strip()
        
        if not os.path.exists(percorso_root):
            return f"<h3>Errore Percorso:</h3><p>La cartella specificata non esiste sul computer/server: <b>{percorso_root}</b></p><br><a href='/'>Riprova</a>", 400

        config, msg_ricerca = trova_e_memorizza_cartelle(percorso_root, nome_cliente, api_key_inserita)
        chiave_attiva = config.get('api_key')
        
        if not chiave_attiva:
            return "Errore: API Key mancante o non valida.", 400
        
        client = genai.Client(api_key=chiave_attiva)

        dati_ee = []
        dati_gas = []

        cartella_ee = config.get("ee")
        if cartella_ee and os.path.exists(cartella_ee):
            for f in os.listdir(cartella_ee):
                if f.lower().endswith('.pdf'):
                    percorso_pdf = os.path.join(cartella_ee, f)
                    dati = estrai_dati_da_pdf(client, percorso_pdf, "energia elettrica")
                    dati['consumo_kwh_convertito'] = converti_in_kwh(dati)
                    dati_ee.append(dati)
                    
        cartella_gas = config.get("gas")
        if cartella_gas and os.path.exists(cartella_gas):
            for f in os.listdir(cartella_gas):
                if f.lower().endswith('.pdf'):
                    percorso_pdf = os.path.join(cartella_gas, f)
                    dati = estrai_dati_da_pdf(client, percorso_pdf, "gas")
                    dati['consumo_kwh_convertito'] = converti_in_kwh(dati)
                    dati_gas.append(dati)

        if not cartella_ee and not cartella_gas:
            return f"""
            <div style="font-family: Arial; padding: 20px; max-width: 600px; margin: 0 auto;">
                <h3 style="color: #d32f2f;">Cartelle non trovate</h3>
                <p>Impossibile individuare automaticamente cartelle con nomi contenenti 'energia', 'elettric', 'gas' o 'metano' all'interno di:</p>
                <p><b>{percorso_root}</b></p>
                <br><a href="/">Torna alla home e verifica la struttura delle cartelle</a>
            </div>
            """, 400

        nome_file_excel = f"Report_Consumi_{nome_cliente}.xlsx"
        with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
            if dati_ee:
                pd.DataFrame(dati_ee).to_excel(writer, sheet_name='Energia_Elettrica', index=False)
            if dati_gas:
                pd.DataFrame(dati_gas).to_excel(writer, sheet_name='Gas', index=False)
            if not dati_ee and not dati_gas:
                pd.DataFrame([{"Note": "Nessun file PDF trovato nelle cartelle individuate"}]).to_excel(writer, sheet_name='Vuoto', index=False)

        return f"""
        <div style="font-family: Arial; padding: 40px; max-width: 600px; margin: 0 auto; text-align: center;">
            <h3 style="color: #2e7d32; font-size: 1.5em;">Processo Completato con Successo!</h3>
            <p style="color: #555;">{msg_ricerca}</p>
            <p style="color: #555;">Documenti analizzati e convertiti in kWh.</p>
            <p style="font-size: 1.1em;">File Excel generato: <b>{nome_file_excel}</b></p>
            <br>
            <a href="/" style="background: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold;">Torna alla home</a>
        </div>
        """
    except Exception as err:
        return f"""
        <div style="font-family: Arial; padding: 20px; max-width: 700px; margin: 0 auto; background: #fff3f3; border: 1px solid #ffcdd2; border-radius: 4px;">
            <h3 style="color: #c62828;">Dettaglio Errore:</h3>
            <p style="font-family: monospace; background: #fff; padding: 10px; border: 1px solid #ddd; word-break: break-all;">{type(err).__name__}: {str(err)}</p>
            <br><a href="/">Torna indietro</a>
        </div>
        """, 500

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)
