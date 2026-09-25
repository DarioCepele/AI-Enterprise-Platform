# Python

Tipizzazione dinamica con annotazioni opzionali introdotte da PEP 484. Le
annotazioni non sono verificate a runtime dall'interprete: servono a strumenti
esterni come mypy e pyright.

La concorrenza convive con il Global Interpreter Lock: i thread non eseguono
bytecode Python in parallelo, quindi il parallelismo su CPU passa da
multiprocessing. Per l'I/O concorrente si usa asyncio, con un event loop a
singolo thread. Dalla 3.13 esiste una build free-threaded senza GIL, ancora
sperimentale.

Gli errori sono eccezioni: si sollevano, si propagano lungo lo stack e si
catturano con try/except. Lo stile idiomatico e' EAFP -- provare e gestire il
fallimento -- invece di controllare prima.

Ecosistema molto ampio: scientifico (NumPy, pandas), web (Django, FastAPI),
machine learning (PyTorch). Il packaging e' storicamente il punto debole,
migliorato da strumenti recenti come uv e Poetry.
