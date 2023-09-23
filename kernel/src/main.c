#include "functions.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int* BLOCK;

int main(int argc, char **argv){

    if(argc < 5){
        printf("Too few arguments.  Arguments: [DIM_X] [DIM_Y] [HEIGHT] [XYZ FILE]");
        return 1;
    }else{
        printf("%s\n", argv[0]);
        printf("DIM_X: %s\n", argv[1]);
        printf("DIM_Y: %s\n", argv[2]);
        printf("HEIGHT: %s\n", argv[3]);
        printf("XYZ FILE: %s\n", argv[4]);
    }

    int DIM_X = atoi(argv[1]);
    int DIM_Y = atoi(argv[2]);
    int HEIGHT = atoi(argv[3]);
    char *FILENAME = argv[4];

    if(DIM_X == 0 || DIM_Y == 0 || HEIGHT == 0){
        printf("Invalid value passed for DIM_X, DIM_Y, or HEIGHT.\n");
        return 1;
    }

	printf("Starting setup...\n");
	setup(DIM_X, DIM_Y, HEIGHT);

	printf("Setup done\n");

	// Tests cutting the structure (removing a circle)
	
	printf("Starting up toy cut...");

	int success = write_block(BLOCK, DIM_X, DIM_Y, "toy.block");
	if(success != 0){
		printf("ERROR - write failed.\n");
		return 1;

	}
	
	success = process_from_file(BLOCK, DIM_X, DIM_Y, FILENAME);

	printf("Done.\n");

	return 0;
}
