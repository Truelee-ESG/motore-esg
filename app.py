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

MESI_ORDINE = {
    'Gennaio': 1, 'Febbraio': 2, 'Marzo': 3, 'Aprile': 4,
    'Maggio': 5, 'Giugno': 6, 'Luglio': 7, 'Agosto': 8,
    'Settembre': 9, 'Ottobre': 10, 'Novembre': 11, 'Dicembre': 12
}

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

def formatta_numero_italiano(val_str):
    v = val_str.replace(" ", "")
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    elif ',' in v:
        v = v.replace(',', '.')
    elif '.' in v:
        parts = v.split('.')
        if len(parts[-1]) == 3:
            v = v.replace('.', '')
    return float(v)

def estrai_dati_locale(percorso_file, categoria, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Analisi avanzata prima pagina: {nome_file}")
    
    testo_prima_pagina = ""
    try:
        reader = PdfReader(percorso_file)
        if len(reader.pages) > 0:
            testo_prima_pagina = reader.pages[0].extract_text() or ""
    except Exception:
        q.put(f"   [Errore] Impossibile leggere {nome_file}.")
        return None
        
    testo_p1_lower = testo_prima_pagina.lower()
    
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

    candidati = []
    unita_misura = "KWH" if categoria == 'energia_elettrica' else "Litri"
    
    # Pattern esteso per catturare numeri interi o decimali formattati
    matches = re.finditer(r'\b(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d+)?)\b', testo_p1_lower)
    
    for m in matches:
        val_str = m.group(1)
        if val_str in ['2022', '2023', '2024', '2025', '2026', '2027', '2028']:
            continue
            
        try:
            num = formatta_numero_italiano(val_str)
            if num <= 0 or num > 500000:  
                continue
                
            # Finestra di contesto allargata a 120 caratteri per catturare tabelle riepilogative
            start = max(0, m.start() - 120)
            end = min(len(testo_p1_lower), m.end() + 120)
            contesto = testo_p1_lower[start:end]
            
            score = 0
            
            if categoria == 'energia_elettrica':
                if 'kwh' in contesto or 'kw/h' in contesto: score += 70
                if any(w in contesto for w in ['consumo', 'totale', 'fatturat', 'periodo', 'prelevata']): score += 60
                if any(w in contesto for w in ['energia', 'attiva', 'f1', 'f2', 'f3', 'totali']): score += 40
                
                # Penalità severe per importi in euro e dati catastali/tecnici del contatore
                if any(w in contesto for w in ['€', 'euro', 'importo', 'spesa', 'totale documento', 'totale da pagare']): score -= 200
                if any(w in contesto for w in ['lettura', 'precedente', 'attuale', 'potenza', 'impegnata', 'disponibile', 'pod', 'cliente', 'codice', 'matricola', 'tensione']): score -= 180
                if num in [1.5, 3.0, 3.3, 4.5, 6.0, 10.0, 13.2, 15.0, 16.5, 20.0, 30.0]: score -= 150
                
            elif categoria == 'trasporti':
                if any(w in contesto for w in ['litri', 'lt', 'l ']): score += 60
                if any(w in contesto for w in ['quantit', 'volume', 'erogata', 'totale']): score += 50
                if '€' in contesto or 'euro' in contesto or 'importo' in contesto: score -= 150
                if any(w in contesto for w in ['km', 'chilometri', 'targa', 'sconto']): score -= 150
                
            if score > 0:
                candidati.append({'valore': num, 'score': score})
        except:
            continue
            
    candidati_unici = {}
    for c in candidati:
        v = c['valore']
        if v not in candidati_unici or c['score'] > candidati_unici[v]:
            candidati_unici[v] = c['score']
            
    lista_candidati = [{'valore': k, 'score': v} for k, v in candidati_unici.items()]
    lista_candidati.sort(key=lambda x: x['score'], reverse=True)
    
    quantita = lista_candidati[0]['valore'] if lista_candidati else 0.0

    tipo_carburante = ""
    if categoria == 'trasporti':
        if any(w in testo_p1_lower for w in ['gasolio', 'diesel', 'f.o.', 'gas.']):
            tipo_carburante = "Gasolio"
        elif any(w in testo_p1_lower for w in ['benzina', 'verde', 'super']):
            tipo_carburante = "Benzina"
        else:
            tipo_carburante = "Non specificato"

    q.put(f"   [OK] Estratto: {quantita} {unita_misura}" + (f" (Score: {lista_candidati[0]['score']})" if lista_candidati else ""))
    
    risultato = {
        "nome_file": nome_file,
        "mese": mese_trovato,
        "anno": anno_trovato,
        "quantita": quantita,
        "unita_misura": unita_misura,
        "candidati_alternativi": lista_candidati
    }
    if categoria == 'trasporti':
        risultato["tipo_carburante"] = tipo_carburante
        
    return risultato

HTML_PAGE = """



    
    
    


    

        

            
Motore ESG 
Estrazione Consumi Multi-Fornitore

            
            Nome Azienda/Cliente (senza spazi):
            
            
            Percorso Server/Cartella Principale (Root):
            

            

                

                    
Ambiente - Energia Elettrica

                    
Estrai kWh (Analisi mirata su Prima Pagina).

                    Avvia Energia Elettrica
                

                
                

                    
Ambiente - Trasporti

                    
Estrai litri e tipo carburante.

                    Avvia Trasporti
                

            

        


        
            
            
            
        
    



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

            q.put(f"Trovati {len(file_paths)} file PDF. Estrazione in corso...")

            dati_estratti = []
            for p in file_paths:
                res = estrai_dati_locale(p, categoria, q)
                if res:
                    dati_estratti.append(res)

            valori_validi = [d['quantita'] for d in dati_estratti if d['quantita'] > 0]
            
            if len(valori_validi) >= 3:
                mediana_consumi = statistics.median(valori_validi)
                soglia_min = mediana_consumi * 0.10
                soglia_max = mediana_consumi * 5.0
                
                q.put(f"\n--- FASE 2: Validazione Statistica (Mediana: {mediana_consumi:.2f}) ---")
                
                for d in dati_estratti:
                    qta = d['quantita']
                    if qta > 0 and (qta < soglia_min or qta > soglia_max):
                        q.put(f" [!] Mese {d['mese']}: Valore {qta} anomalo. Ricerca alternative...")
                        sostituito = False
                        for alt in d.get('candidati_alternativi', []):
                            if alt['valore'] != qta and alt['score'] > 15 and (soglia_min <= alt['valore'] <= soglia_max):
                                d['quantita'] = alt['valore']
                                q.put(f"     -> [CORRETTO] Sostituito con: {alt['valore']}")
                                sostituito = True
                                break
                        if not sostituito:
                            q.put(f"     -> [MANTENUTO] Nessuna alternativa migliore.")
            
            for d in dati_estratti:
                if 'candidati_alternativi' in d:
                    del d['candidati_alternativi']

            if dati_estratti:
                df = pd.DataFrame(dati_estratti)
                df['num_mese'] = df['mese'].map(MESI_ORDINE).fillna(13)
                df = df.sort_values(by=['anno', 'num_mese']).drop(columns=['num_mese'])
                dati_estratti_ordinati = df.to_dict('records')
            else:
                dati_estratti_ordinati = []

            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_{categoria.capitalize()}_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_estratti_ordinati:
                    pd.DataFrame(dati_estratti_ordinati).to_excel(writer, sheet_name=categoria.capitalize(), index=False)
                else:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: Report Excel generato sul Desktop!")
            q.put(("DONE", nome_file_excel))
        except Exception as err:
            import traceback
            q.put(f"ERRORE CRITICO:\n{traceback.format_exc()}")
            q.put(("DONE", None))

    threading.Thread(target=background_worker).start()

    def generate():
        yield f"""
        
        
        
            
            
            
        
        
            

                
Estrazione Consumi {categoria.replace('_', ' ').title()}

                
                
            

        
        
        """
        
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "DONE":
                file_path = item[1]
                if file_path:
                    yield f""
                else:
                    yield f""
                break
            else:
                safe_msg = str(item).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                yield f"\n"

    return Response(generate(), mimetype='text/html')

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)

