# Architecture

ALWAYS distinguish between the overlay and the coach.

The overlay is responsible for displaying candidate moves and visual feedback.

The coach is responsible for explaining ideas, plans and strategic concepts.



# Stability

NEVER modify the overlay behaviour unless the requested feature explicitly requires it.




# Stockfish/Berserker

PREFER extracting more information from an existing analysis over increasing depth or making additional engine calls.