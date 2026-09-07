---
name: comparison
description: Confronta piu' elementi lungo dimensioni comuni e rende il risultato in tabella.
---

# Confronto strutturato

Quando l'utente chiede di confrontare due o piu' cose:

1. Individua le **dimensioni** del confronto. Se l'utente le ha nominate, usa
   quelle e non aggiungerne. Se non le ha nominate, scegline tre o quattro che
   distinguano davvero gli elementi.
2. Chiama `ui_table` con una colonna per la dimensione e una colonna per
   ciascun elemento confrontato.
3. Dopo la tabella scrivi due o tre righe che dicano **cosa cambia davvero**,
   non che ripetano le celle.

Non descrivere il confronto a parole prima di aver chiamato `ui_table`:
l'utente vede la tabella comparire, e ripeterla nel testo la rende rumore.
