# Generate demo GIFs from VHS tape scripts.
#
# Usage:
#   make demos          # Build all demos
#   make quickstart     # Build one demo
#   make clean          # Remove generated GIFs

TAPES := $(wildcard *.tape)
GIFS  := $(TAPES:.tape=.gif)

.PHONY: demos clean

demos: $(GIFS)

%.gif: %.tape
	vhs $<

quickstart: quickstart.gif
tools: tools.gif
exec-mode: exec-mode.gif

clean:
	rm -f *.gif
