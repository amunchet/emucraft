#include "../include/functions.h"

int test_create_structure(){
	// Tests creating the structure
}

int test_freeing_structure(){
	// Tests tearing down the structure
}


// Variables
int DIM_X;
int DIM_Y;

int HEIGHT; // Max is Int16, which is ~30k or 30" in the real world

int CUTTER_DIAMETER;  	
int CUTTER_HEIGHT;
int CUTTER_X;
int CUTTER_Y;

int* BLOCK;


int setup(){
	// Sets up the variables
	DIM_X = 1000;
	DIM_Y = 1000;

	HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world
	
	CUTTER_DIAMETER = 500;  	// .500"
	CUTTER_HEIGHT = 750;		// .750"
	
	CUTTER_X = 100;
	CUTTER_Y = 100;
}
int test_cut(){
	// Tests cutting the structure (removing a circle)

	if(BLOCK[50 * DIM_X + 50] != 1000){
		printf("ERROR - block not initialized properly.");
		return 1;
	}
	
	int success = cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT);
	if(success != 0){
		printf("ERROR - block not cut properly.");
		return 1;

	}
	if(BLOCK[50 * DIM_X + 50] != 750){
		printf("ERROR - block not cut to right height.");
		return 1;
	}
	
	return 0;

}
int test_correct_circle_boundary(){
	// Test that only within the circle is cut
	// Test something outside the circle being cut

	// Test the values of the block being lower than where the circle is cutting
	
	// Test to make sure that a value is returned when the block has been cut
	// Test to make sure a different value is returned when the block has not

	// Test to make sure the difference (delta) is returned somewhere

}

int test_read_in_file(){
	// Tests reading in a processed file (not G-code, the XYZ file plus cutting information)

	// What if the file is bad formatting?

}



int main(){
	setup();
	return test_cut();
}

