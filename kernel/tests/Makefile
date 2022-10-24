# build an executable named myprog from myprog.c
all: test_emucraft.c 
	gcc -g -Wall -o test_emucraft.so test_emucraft.c -lm
	gcc -g -Wall -o test_file.so test_file.c -lm

toy: toy.c
	gcc -g -Wall -o toy.so toy.c -lm

clean: 
	$(RM) test_emucraft.so
	$(RM) test_file.so

