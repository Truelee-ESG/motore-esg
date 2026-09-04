import os
import re
import json
import time
import threading
import queue
import webbrowser
import pandas as pd
from pypdf import PdfReader
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

# ==========================================
# 1. RICERCA CARTELLA PER CATEGORIA
# ==========================================
def trova_cartella_categoria(percorso_root, categoria):
    percorso_root = percorso_root.strip('"\'').strip()
    if not os.path.exists(percorso_root):
        return percorso_root
        
    candidata = percorso_root
    for root, dirs, files in os.walk(percorso_root):
        for directory in dirs:
            nome_dir = directory.lower()
            if categoria == 'energia_elettrica':
                if any(k in nome_dir for k in ["energia", "elettric", "e.e", "luce"]):
                    return os.path.join(root, directory)
            elif categoria == 'trasporti':
                if any(k in nome_dir for k in ["trasporti", "gasolio", "benzina", "carburante", "auto", "furgoni", "mezzi"]):
                    return os.path.join(root, directory)
    return candidata

# ==========================================
# 2. MOTORE DI ESTRAZIONE POTENZIATO (PRIMA PAGINA)
# ==========================================
def estrai_dati_locale(percorso_file, categoria, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Analisi approfondita prima pagina: {nome_file}")
    
    testo_prima_pagina = ""
    testo_completo = ""
    try:
        reader = PdfReader(percorso_file)
        if len(reader.pages) > 0:
            testo_prima_pagina = reader.pages[0].extract_text() or ""
        for page in reader.pages:
            estratto = page.extract_text()
            if estratto:
                testo_completo += estratto + "\n"
    except Exception as e:
        q.put(f"   [Errore Lettura] Impossibile aprire il file {nome_file}.")
        return None
        
    if not testo_prima_pagina.strip():
        q.put(f"   [Avviso] La prima pagina del PDF {nome_file} è vuota o scansionata.")
        
    testo_p1_lower = testo_prima_pagina.lower()
    testo_tot_lower = testo_completo.lower()
    
    # --- 1. TROVA MESE E ANNO ---
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    mese_trovato = "Sconosciuto"
    anno_trovato = 2026
    
    for m in mesi:
        if m in nome_file.lower():
            mese_trovato = m.capitalize()
            break
            
    match_anno = re.search(r"(202\d)", nome_file)
    if match_anno:
        anno_trovato = int(match_anno.group(1))
        
    if mese_trovato == "Sconosciuto":
        for m in mesi:
            if m in testo_p1_lower:
                mese_trovato = m.capitalize()
                break
    if not match_anno:
        match_anno_testo = re.search(r"(202\d)", testo_p1_lower)
        if match_anno_testo:
            anno_trovato = int(match_anno_testo.group(1))

    # --- 2. ESTRAZIONE QUANTITÀ E UNITÀ MIRATA SULLA PRIMA PAGINA ---
    quantita = 0.0
    unita_misura = ""

    # Cerchiamo prima pattern strutturati vicini a parole chiave di consumo/quantità
    # Esempio: "consumo 4.829 kWh" oppure "totale kwh 4829"
    pattern_mirati = [
        r"(?:consum[oi]|totale|prelevata|energia attiva|quantit[aà]|q\.t[aà]|litri|volume|fatturat[oa])[\s\S]{0,40}?([\d\.,]+)\s*(kwh|litri|lt|l|sm3|m3|mc|kg)?",
        r"([\d\.,]+)\s*(kwh|litri|lt|l|sm3|m3|mc|kg)"
    ]

    candidati = []
    for pat in pattern_mirati:
        matches = re.findall(pat, testo_p1_lower)
        if matches:
            for match in matches:
                val_str = match[0] if isinstance(match, tuple) else match
                unit_str = match[1] if isinstance(match, tuple) and len(match) > 1 else ""
                
                # Pulizia formato numerico italiano (es. 4.829 o 4.829,00 -> 4829.0)
                val_clean = val_str.replace('.', '').replace(',', '.')
                try:
                    num = float(val_clean)
                    # Filtriamo numeri troppo piccoli (es. date, civici) o codici fiscali/partite iva troppo lunghi
                    if 0.5 < num < 1000000:
                        candidati.append((num, unit_str.upper()))
                except:
                    continue

    if candidati:
        # Se troviamo candidati mirati, prendiamo il primo sensato o il più alto legato a unità note
        for num, unit in candidati:
            if unit: # Priorità a chi ha l'unità esplicita (es. kWh)
                quantita = num
                unita_misura = unit
                break
        if quantita == 0.0 and candidati:
            # Fallback sul primo numero valido trovato vicino alle keyword
            quantita = candidati[0][0]
            unita_misura = candidati[0][1]

    # Se l'unità non è stata intercettata ma la categoria è nota, diamo un'unità di default coerente
    if not unita_misura:
        if categoria == 'energia_elettrica':
            unita_misura = "kWh"
        elif categoria == 'trasporti':
            unita_misura = "Litri"

    # --- 3. SPECIFICO PER TRASPORTI (GASOLIO / BENZINA) ---
    tipo_carburante = ""
    if categoria == 'trasporti':
        if any(w in testo_tot_lower for w in ['gasolio', 'diesel', 'f.o.', 'gas.']):
            tipo_carburante = "Gasolio"
        elif any(w in testo_tot_lower for w in ['benzina', 'verde', 'super']):
            tipo_carburante = "Benzina"
        else:
            tipo_carburante = "Non specificato"

    q.put(f"   [OK] Estratto: {quantita} {unita_misura}" + (f" ({tipo_carburante})" if tipo_carburante else ""))
    
    risultato = {
        "nome_file": nome_file,
        "mese": mese_trovato,
        "anno": anno_trovato,
        "quantita": quantita,
        "unita_misura": unita_misura
    }
    
    if categoria == 'trasporti':
        risultato["tipo_carburante"] = tipo_carburante

    return risultato

# ==========================================
# 3. INTERFACCIA WEB (DASHBOARD)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard ESG - Estrazione Locale</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px; color: #333;}
        .container { max-width: 900px; margin: 0 auto; }
        .box { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 8px 0 20px 0; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: #fafbfc; padding: 25px; border-radius: 8px; border: 1px solid #e1e4e8; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
        .card h3 { margin-top: 0; color: #2e7d32; }
        .btn { padding: 12px 20px; background: #2e7d32; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 1em; width: 100%; margin-top: 15px; transition: background 0.3s; }
        .btn:hover { background: #1b5e20; }
        h2 { text-align: center; color: #2e7d32; margin-top: 0; }
        label { font-weight: bold; font-size: 0.9em; color: #333; display: block; text-align: left; }
    </style>
    <script>
        function avviaEstrazione(categoria) {
            const cliente = document.getElementById('nome_cliente').value.trim();
            const percorso = document.getElementById('percorso_root').value.trim();
            
            if(!cliente || !percorso) {
                alert("Inserisci prima il Nome Azienda/Cliente e il Percorso della cartella!");
                return;
            }
            
            document.getElementById('form_cliente').value = cliente;
            document.getElementById('form_percorso').value = percorso;
            document.getElementById('form_categoria').value = categoria;
            document.getElementById('hiddenForm').submit();
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="box">
            <h2>Motore ESG <br><small style="font-size: 0.5em; color: #666;">Analisi Prima Pagina & Algoritmo Ottimizzato</small></h2>
            
            <label>Nome Azienda/Cliente (senza spazi):</label>
            <input type="text" id="nome_cliente" placeholder="es. ditta_rossi" required>
            
            <label>Percorso Server/Cartella Principale (Root):</label>
            <input type="text" id="percorso_root" placeholder="es. C:\\Archivio_Dati" required>

            <div class="grid">
                <div class="card">
                    <h3>Ambiente - Energia Elettrica</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai le bollette elettriche in un file Excel dedicato.</p>
                    <button class="btn" onclick="avviaEstrazione('energia_elettrica')">Avvia Energia Elettrica</button>
                </div>
                
                <div class="card">
                    <h3>Ambiente - Trasporti</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai le fatture di carburante (Gasolio/Benzina) in un file Excel dedicato.</p>
                    <button class="btn" onclick="avviaEstrazione('trasporti')">Avvia Trasporti</button>
                </div>
            </div>
        </div>

        <form id="hiddenForm" action="/avvia" method="POST" style="display: none;">
            <input type="hidden" name="nome_cliente" id="form_cliente">
            <input type="hidden" name="percorso_root" id="form_percorso">
            <input type="hidden" name="categoria" id="form_categoria">
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
    categoria = request.form['categoria'].strip()
    q = queue.Queue()

    def background_worker():
        try:
            if not os.path.exists(percorso_root):
                q.put(f"ERRORE: La cartella non esiste: {percorso_root}")
                q.put(("DONE", None))
                return

            q.put(f"Inizializzazione estrazione per: {categoria.replace('_', ' ').upper()}")
            
            cartella_target = trova_cartella_categoria(percorso_root, categoria)
            q.put(f"Cartella analizzata: {cartella_target}")

            file_paths = []
            if os.path.exists(cartella_target):
                for f in os.listdir(cartella_target):
                    if f.lower().endswith('.pdf'):
                        file_paths.append(os.path.join(cartella_target, f))

            if not file_paths:
                q.put(f"ATTENZIONE: Nessun file PDF trovato in {cartella_target}.")
                q.put(("DONE", None))
                return

            q.put(f"Trovati {len(file_paths)} file PDF. Analisi prima pagina in corso...")

            dati_estratti = []
            for p in file_paths:
                res = estrai_dati_locale(p, categoria, q)
                if res:
                    dati_estratti.append(res)

            q.put("\n--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_{categoria.capitalize()}_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_estratti:
                    pd.DataFrame(dati_estratti).to_excel(writer, sheet_name=categoria.capitalize(), index=False)
                else:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto!")
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
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 350px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #2e7d32; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #2e7d32; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
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
                <h2>Elaborazione {categoria.replace('_', ' ').title()} <br><small style="color:#666; font-size:0.6em;">Algoritmo Ottimizzato Prima Pagina</small></h2>
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
