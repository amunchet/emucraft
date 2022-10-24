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
int DIM_X = 10;
int DIM_Y = 10;

int HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world

int CUTTER_DIAMETER = 3;  	
int CUTTER_HEIGHT = 750;		

int CUTTER_X = 5;
int CUTTER_Y = 5;



int setup(){
	printf("Starting setup...\n");
	BLOCK = malloc ((DIM_X * DIM_Y) * sizeof(int));
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = 1000;

	}
	// Print 10 x 10 block
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);
	printf("Done\n");
	return 0;
}


int test_read_in_file(){
	// Tests reading in a processed file (not G-code, the XYZ file plus cutting information)

	// What if the file is bad formatting?
	return 0;
}

int test_write_file(){
	// Tests writing out to a file
	printf("Starting write_file test...\n");
	BLOCK[0] = 750;
	BLOCK[5* DIM_X + 1] = 750;
	int output = write_block(BLOCK, DIM_X, DIM_Y, "test.block");

	if(output != 0){
		printf("Write Block failed\n");
		return 1;
	}
	printf("Done with writing block.\n");
	FILE *fp = fopen("test.block", "r");
	
	printf("Opening test.block...\n");

	for(int x= 0; x< 10; x++){
		int line[10];
		
		fscanf(fp,"%d %d %d %d %d %d %d %d %d %d\n", &line[0], &line[1], &line[2], &line[3], &line[4], &line[5], &line[6], &line[7], &line[8], &line[9]);
		for(int y=0; y<10; y++){
			if(line[y] != BLOCK[x * DIM_X + y]){
				printf("Error - Block and read block don't match: ");
				printf("%d vs %d\n", line[y], BLOCK[x * DIM_X + y]);
				return 1;
			}
		}
		
	}
	fclose(fp);
	printf("Completed\n");
	return 0;
}

int main(){
	int output = setup();
	if(output != 0){
		printf("Setup failed\n");
		return output;
	}
	output = test_write_file();
	if(output != 0){
		printf("Test Write File failed.\n");
		return output;
	}
}	
