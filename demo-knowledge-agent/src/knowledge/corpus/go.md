# Go

Tipizzazione statica verificata dal compilatore, con inferenza locale tramite
`:=`. Non esistono generici prima della 1.18; da li' in poi ci sono, ma il
codice idiomatico resta poco parametrico.

La concorrenza e' un costrutto del linguaggio: le goroutine sono leggere e le
schedula il runtime su piu' core, la comunicazione passa dai channel secondo il
modello CSP. Il tooling include un race detector integrato.

Gli errori sono valori restituiti, non eccezioni: la firma `(T, error)` e il
controllo `if err != nil` sono la norma. `panic`/`recover` esistono ma sono
riservati ai fallimenti non recuperabili.

Compila in un binario statico singolo, il che rende il deploy banale. La
libreria standard copre HTTP, crittografia e testing senza dipendenze esterne.
