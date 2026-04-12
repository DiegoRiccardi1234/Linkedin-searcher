# Checklist Iniziale (2 Minuti)

Usa questa checklist per verificare in 2 minuti che tutto funzioni sul PC del tuo amico.

1. Apri progetto e aggiorna codice
- git pull

2. Installa dipendenze
- python -m pip install -r requirements.txt

3. Avvia app
- python run_webapp.py
- Apri: http://127.0.0.1:8000

4. Configura key dalla UI
- Sezione: 0) Configura API Key
- Inserisci almeno una key (Cerebras o Groq)
- Premi Salva Key

5. Controllo rapido backend
- In terminale:
- Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 8
- Deve essere ok true e active_provider diverso da none

6. Test funzionale super veloce
- Carica un CV (md/pdf/docx)
- Aggiungi un annuncio manuale
- Premi Dettaglio su quell'annuncio
- Invia un messaggio in chat: consigliami i migliori 3

Se tutti i punti passano, l'app e pronta.
