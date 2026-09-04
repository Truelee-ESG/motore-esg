import os
import json
import threading
import webbrowser
import pandas as pd
import google.generativeai as genai
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

    if not config.get("ee") or not config.get("gas") or not os.path.exists(str(config.get("ee", ""))):
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
# 2. LOGICA ESTRAZIONE AUTORICARICANTE
# ==========================================
def trova_modello_compatibile():
    """Interroga i server Google in tempo reale per trovare il modello Flash più recente disponibile"""
    try:
        modelli_disponibili = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelli_disponibili.append(m.name)
        
        # Cerca un modello flash
        for m in modelli_disponibili:
            if 'flash' in m.lower():
                return m.replace('models/', '')
                
        # Se c'è un qualsiasi altro modello valido
        if modelli_disponibili:
            return modelli_disponibili[0].replace('models/', '')
    except Exception:
        pass
    
    # Fallback di sicurezza blindato
    return 'gemini-3.6-flash'

def estrai_dati_da_pdf(percorso_file, tipo_bolletta):
    with open(percorso_file, "rb") as doc_file:
        pdf_bytes = doc_file.read()
        
    pdf_part = {
        "mime_type": "application/pdf",
        "data": pdf_bytes
    }
        
    prompt = f"""
    Leggi questa bolletta di {tipo_bolletta}.
    Estrai i dati e rispondi SOLO con un oggetto JSON valido con questa struttura:
    {{"mese": "gennaio", "anno": 2026, "consumo": 120.5, "unita_misura": "sm3", "tipo_gas": "metano"}}
    Se l'unità di misura è kWh, inserisci "kWh". Se è energia elettrica, tipo_gas deve essere vuoto "".
    """
    
    # Costruiamo la lista combinando il modello trovato dinamicamente e le riserve fisse
    modelli_da_testare = []
    modello_dinamico = trova_modello_compatibile()
    if modello_dinamico:
        modelli_da_testare.append(modello_dinamico)
        
    for m in ['gemini-3.6-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']:
        if m not in modelli_da_testare:
            modelli_da_testare.append(m)
            
    ultimo_errore = None
    for nome_modello in modelli_da_testare:
        try:
            model = genai.GenerativeModel(nome_modello)
            response = model.generate_content([prompt, pdf_part])
            testo_pulito = response.text.strip().replace('```json', '').replace('
