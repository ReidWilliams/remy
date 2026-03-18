BINARY = remy-helper
SRC    = swift/main.swift

build:
	swiftc -swift-version 5 $(SRC) -o $(BINARY)

clean:
	rm -f $(BINARY)

run: build
	python3 remy.py

.PHONY: build clean run
