import os
import re
import json
import time
import threading
import queue
import webbrowser
import statistics
import pandas as pd
from pypdf import PdfReader
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

# Mappa per l'ordinamento cronologico dei mesi
MESI_ORDINE = {
    'Gennaio': 1, 'Febbraio': 2, 'Marzo': 3, 'Aprile': 4,
    'Maggio': 5, 'Giugno': 6, 'Luglio': 7, 'Agosto': 8,
    'Settembre': 9, 'Ottobre': 10, 'Novembre': 11, 'Dicembre': 12
}

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
# FUNZIONE SUPPORTO: Lettura corretta numeri italiani
# ==========================================
def formatta_numero_italiano(val_str):
    v = val_str.replace(" ", "")
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.') # Es: 1.234,56 -> 1234.56
    elif ',' in v:
        v = v.replace(',', '.') # Es: 1234,56 -> 1234.56
    elif '.' in v:
        parts = v.split('.')
        # Se ci sono esattamente 3 cifre dopo il punto, in Italia è il separatore delle migliaia (es: 1.500)
        if len(parts[-1]) == 3:
            v = v.replace('.', '')
    return float(v)

# ==========================================
# 2. MOTORE DI ESTRAZIONE A PUNTEGGIO (SOLO PRIMA PAGINA)
# ==========================================
def estrai_dati_locale(percorso_file, categoria, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Analisi documento (Solo Pagina 1): {nome_file}")
    
    testo_prima_pagina = ""
    try:
        reader = PdfReader(percorso_file)
        # LEGGE RIGOROSAMENTE SOLO LA PRIMA PAGINA (indice 0)
        if len(reader.pages) > 0:
            testo_prima_pagina = reader.pages[0].extract_text() or ""
    except Exception:
        q.put(f"   [Errore] Impossibile leggere {nome_file}.")
        return None
        
    testo_p1_lower = testo_prima_pagina.lower()
    
    # Rilevamento Mese e Anno
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    mese_trovato = "Gennaio"
    anno_trovato = 2026
    
    for m in mesi:
        if m in nome_file.lower():
            mese_trovato = m.capitalize()
            break
            
    match_anno = re.search(r"(202\d)", nome_file)
    if match_anno:
        anno_trovato = int(match_anno.group(1))
        
    if mese_trovato == "Gennaio":
        for m in mesi:
            if m in testo_p1_lower:
                mese_trovato = m.capitalize()
                break
    if not match_anno:
        match_anno_testo = re.search(r"(202\d)", testo_p1_lower)
        if match_anno_testo:
            anno_trovato = int(match_anno_testo.group(1))

    # --- ALGORITMO DI SCORING CONTESTUALE ---
    candidati = []
    unita_misura = "KWH" if categoria == 'energia_elettrica' else "Litri"
    
    # Cattura qualsiasi potenziale numero nella prima pagina
    matches = re.finditer(r'\b(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d+)?)\b', testo_p1_lower)
    
    for m in matches:
        val_str = m.group(1)
        
        # Filtra palesemente gli anni solari
        if val_str in ['2022', '2023', '2024', '2025', '2026', '2027']:
            continue
            
        try:
            num = formatta_numero_italiano(val_str)
            # Tagliamo fuori numeri impossibili da essere consumi su singola bolletta
            if num <= 0 or num > 600000:  
                continue
                
            # Finestra di contesto ristretta attorno al numero (50 caratteri)
            start = max(0, m.start() - 50)
            end = min(len(testo_p1_lower), m.end() + 50)
            contesto = testo_p1_lower[start:end]
            
            score = 0
            
            if categoria == 'energia_elettrica':
                # Punti positivi per le parole classiche dei riepiloghi di prima pagina
                if 'kwh' in contesto or 'kw/h' in contesto: score += 60
                if any(w in contesto for w in ['consumo', 'totale', 'fatturat', 'periodo']): score += 50
                if any(w in contesto for w in ['energia', 'attiva', 'prelevata']): score += 30
                
                # Penalità durissime: escludiamo i costi in Euro e i dati del contatore
                if '€' in contesto or 'euro' in contesto or 'importo' in contesto or 'spesa' in contesto: score -= 150
                if any(w in contesto for w in ['lettura', 'precedente', 'attuale', 'potenza', 'impegnata', 'disponibile', 'pod', 'cliente', 'codice']): score -= 150
                
                # Penalità per le costanti di potenza (es. 3.0, 4.5, 6.0 kW)
                if num in [3.0, 3.3, 4.5, 6.0, 10.0, 15.0]: score -= 100
                
            elif categoria == 'trasporti':
                if any(w in contesto for w in ['litri', 'lt', 'l ']): score += 60
                if any(w in contesto for w in ['quantit', 'volume', 'erogata', 'totale']): score += 50
                
                if '€' in contesto or 'euro' in contesto or 'importo' in contesto: score -= 150
                if any(w in contesto for w in ['km', 'chilometri', 'targa', 'sconto']): score -= 150
                
            if score > 0:
                candidati.append({'valore': num, 'score': score})
        except:
            continue
            
    # Gestione candidati per mantenere solo il punteggio massimo per ogni numero distinto
    candidati_unici = {}
    for c in candidati:
        v = c['valore']
        if v not in candidati_unici or c['score'] > candidati_unici[v]:
            candidati_unici[v] = c['score']
            
    # Ordina dal punteggio più alto
    lista_candidati = [{'valore': k, 'score': v} for k, v in candidati_unici.items()]
    lista_candidati.sort(key=lambda x: x['score'], reverse=True)
    
    quantita = lista_candidati[0]['valore'] if lista_candidati else 0.0

    # Rilevamento carburante
    tipo_carburante = ""
    if categoria == 'trasporti':
        if any(w in testo_p1_lower for w in ['gasolio', 'diesel', 'f.o.', 'gas.']):
            tipo_carburante = "Gasolio"
        elif any(w in testo_p1_lower for w in ['benzina', 'verde', 'super']):
            tipo_carburante = "Benzina"
        else:
            tipo_carburante = "Non specificato"

    q.put(f"   [OK] Rilevato provvisorio: {quantita} {unita_misura}" + (f" (Score: {lista_candidati[0]['score']})" if lista_candidati else ""))
    
    risultato = {
        "nome_file": nome_file,
        "mese": mese_trovato,
        "anno": anno_trovato,
        "quantita": quantita,
        "unita_misura": unita_misura,
        "candidati_alternativi": lista_candidati # Utile per il check in Fase 2
    }
    if categoria == 'trasporti':
        risultato["tipo_carburante"] = tipo_carburante
        
    return risultato

# ==========================================
# 3. INTERFACCIA WEB E RUNNER
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Motore ESG - Estrazione Locale</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px; color: #333;}
        .container { max-width: 900px; margin: 0 auto; }
        .box { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
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
            <h2>Motore ESG <br><small style="font-size: 0.5em; color: #666;">Estrazione Consumi Multi-Fornitore</small></h2>
            
            <label>Nome Azienda/Cliente (senza spazi):</label>
            <input type="text" id="nome_cliente" placeholder="es. ditta_rossi" required>
            
            <label>Percorso Server/Cartella Principale (Root):</label>
            <input type="text" id="percorso_root" placeholder="es. C:\\Archivio_Dati" required>

            <div class="grid">
                <div class="card">
                    <h3>Ambiente - Energia Elettrica</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai kWh (Analisi mirata su Prima Pagina).</p>
                    <button class="btn" onclick="avviaEstrazione('energia_elettrica')">Avvia Energia Elettrica</button>
                </div>
                
                <div class="card">
                    <h3>Ambiente - Trasporti</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai litri e tipo carburante.</p>
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

            q.put(f"Trovati {len(file_paths)} file PDF. Estrazione in corso (Modalità Pagina 1)...")

            dati_estratti = []
            
            # --- FASE 1: Scoring su tutti i file (Solo prima pagina) ---
            for p in file_paths:
                res = estrai_dati_locale(p, categoria, q)
                if res:
                    dati_estratti.append(res)

            # --- FASE 2: Safe-Check Storico ed Estrazione Alternativa ---
            valori_validi = [d['quantita'] for d in dati_estratti if d['quantita'] > 0]
            
            if len(valori_validi) >= 3:
                mediana_consumi = statistics.median(valori_validi)
                # Tolleranza ampia: i consumi possono calare molto o triplicare
                soglia_min = mediana_consumi * 0.15
                soglia_max = mediana_consumi * 4.0
                
                q.put(f"\n--- FASE 2: Bilanciamento (Mediana Storica: {mediana_consumi:.2f}) ---")
                
                for d in dati_estratti:
                    qta = d['quantita']
                    
                    if qta > 0 and (qta < soglia_min or qta > soglia_max):
                        q.put(f" [!] Mese {d['mese']}: Il valore {qta} risulta anomalo. Scansione alternative in pag 1...")
                        
                        sostituito = False
                        for alt in d.get('candidati_alternativi', []):
                            alt_val = alt['valore']
                            alt_score = alt['score']
                            
                            # Se l'alternativa ha senso logico ed è vicina alla mediana, sostituiscila
                            if alt_val != qta and alt_score > 10 and (soglia_min <= alt_val <= soglia_max):
                                d['quantita'] = alt_val
                                q.put(f"     -> [CORRETTO] Trovata ottima alternativa nella sintesi: {alt_val}")
                                sostituito = True
                                break
                                
                        if not sostituito:
                            q.put(f"     -> [IGNORATO] Nessuna alternativa migliore. Mantengo {qta}.")
            
            # Pulizia dizionario
            for d in dati_estratti:
                if 'candidati_alternativi' in d:
                    del d['candidati_alternativi']

            # --- ORDINAMENTO CRONOLOGICO ---
            if dati_estratti:
                df = pd.DataFrame(dati_estratti)
                df['num_mese'] = df['mese'].map(MESI_ORDINE).fillna(13)
                df = df.sort_values(by=['anno', 'num_mese']).drop(columns=['num_mese'])
                dati_estratti_ordinati = df.to_dict('records')
            else:
                dati_estratti_ordinati = []

            q.put("\n--- Generazione file Excel ordinato sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_{categoria.capitalize()}_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_estratti_ordinati:
                    pd.DataFrame(dati_estratti_ordinati).to_excel(writer, sheet_name=categoria.capitalize(), index=False)
                else:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto e ordinato cronologicamente!")
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
                <h2>Estrazione Consumi {categoria.replace('_', ' ').title()}</h2>
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
