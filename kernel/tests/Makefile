# build an executable named myprog from myprog.c
all: test_emucraft.c 
	gcc -g -Wall -o test_emucraft.so test_emucraft.c ../src/functions.c -lm
	gcc -g -Wall -o test_file.so test_file.c ../src/functions.c -lm

test: all
	./test_emucraft.so > test_emucraft.log || (tail -20 test_emucraft.log; exit 1)
	./test_file.so > test_file.log || (tail -20 test_file.log; exit 1)
	@echo "Kernel tests passed"

toy: toy.c
	gcc -g -Wall -o toy.so toy.c ../src/functions.c -lm

clean: 
	$(RM) test_emucraft.so
	$(RM) test_file.so
	$(RM) test_emucraft.log test_file.log
