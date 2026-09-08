# Rust

Tipizzazione statica con inferenza estesa e generici monomorfizzati. Il sistema
di tipi include algebraic data type (`enum`) e pattern matching esaustivo.

La memoria non ha garbage collector: ownership e borrowing sono verificati dal
compilatore, che rifiuta i data race a compile time. La concorrenza usa thread
di sistema, piu' async/await con un runtime esterno come Tokio.

Gli errori sono valori: `Result<T, E>` e l'operatore `?` per la propagazione.
`panic!` esiste per gli stati irrecuperabili. Non c'e' `null`: al suo posto
`Option<T>`.

Curva di apprendimento ripida, tempi di compilazione alti. In cambio, prestazioni
paragonabili al C e nessuna classe intera di bug di memoria.
