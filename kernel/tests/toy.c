#include "../include/functions.h"
#include <math.h>
#include <stdio.h>


// Variables
int DIM_X;
int DIM_Y;

int HEIGHT; // Max is Int16, which is ~30k or 30" in the real world

int CUTTER_DIAMETER;  	
int CUTTER_HEIGHT;
int CUTTER_X;
int CUTTER_Y;

int* BLOCK;


// Sets up the variables
DIM_X = 1000;
DIM_Y = 1000;

HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world

CUTTER_DIAMETER = 250;  	
CUTTER_HEIGHT = 750;		

CUTTER_X = 300;
CUTTER_Y = 300;



int setup(){

	BLOCK = malloc ((DIM_X * DIM_Y) * sizeof(int));
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = 1000;

	}
	// Print 10 x 10 block
	//print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);

	return 0;
}





int main(){
	printf("Starting setup...\n");
	setup();

	printf("Setup done\n");

	// Tests cutting the structure (removing a circle)
	
	printf("Starting up toy cut...");

	
	int success = process_from_file(BLOCK, DIM_X, DIM_Y, "toy.xyz");

	
	
	success = write_block(BLOCK, DIM_X, DIM_Y, "toy.block");
	if(success != 0){
		printf("ERROR - write failed.");
		return 1;

	}

	printf("Done.\n");

	return 0;
}

