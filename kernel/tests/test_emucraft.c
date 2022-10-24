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
DIM_X = 10;
DIM_Y = 10;

HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world

CUTTER_DIAMETER = 6;  	
CUTTER_HEIGHT = 750;		

CUTTER_X = 5;
CUTTER_Y = 5;


int check_correct_cut(int x,int y){
	int temp_x;
	int temp_y;

	if(x < 0){
		return -1;
	}else if(x > DIM_X){
		return -1;
	}else{
		temp_x = x;
	}
	
	if(y < 0){
		return -2;
	}else if(y > DIM_Y){
		return -2;
	}else{
		temp_y = y;
	}
	printf("[check_correct_cut] Value of (%d, %d) is %d\n", x, y, BLOCK[temp_x * DIM_X + temp_y]);
	
	return BLOCK[temp_x * DIM_X + temp_y];
}
int setup(){

	BLOCK = malloc ((DIM_X * DIM_Y) * sizeof(int));
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = 1000;

	}
	// Print 10 x 10 block
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);

	if(check_correct_cut(5,5) != 1000){
		printf("ERROR - block not initialized properly: %d\n", check_correct_cut(5,5));
		return 1;
	}
	return 0;
}

int test_cut(){
	// Tests cutting the structure (removing a circle)
	
	printf("Starting up test_cut...");

	
	int success = cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT, DIM_X, DIM_Y);
	if(success != 0){
		printf("ERROR - block not cut properly.");
		return 1;

	}
	return 0;
}



int test_correct_circle_boundary(){
	// Test that only within the circle is cut
	if(check_correct_cut(5,5) != 1000){
		printf("ERROR - block not initialized properly.");
		return 1;
	}


	cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT, DIM_X, DIM_Y);

	
	int x = 0;
	int y = 0;


	// Left Boundary 
	x = CUTTER_X - ceil(CUTTER_DIAMETER/2) + 1;
	y = CUTTER_Y;


	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary (%d, %d) not the right height: %d\n", x, y, check_correct_cut(x,y));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;
	}


	if(check_correct_cut(x,y+3) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 3 (%d, %d) is at the wrong height: %d\n", x, y+3, check_correct_cut(x,y+3));
		
		printf("Block:%d, %d\n", x, y);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}
	if(check_correct_cut(x-1, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary - 1 (%d, %d) is at the wrong height: %d\n", x-1, y, check_correct_cut(x-1, y));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}
	
	// Right Boundary
	x = CUTTER_X + ceil(CUTTER_DIAMETER/2) - 1;
	y = CUTTER_Y;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Top Right Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_X + y]);
		return 1;
	}
	if(check_correct_cut(x,y+3) == CUTTER_HEIGHT){
		printf("ERROR - Top Right Boundary + 3 is at the wrong height: %d\n", check_correct_cut(x,y+3));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}
	if(check_correct_cut(x+1, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x+1, y));
		return 1;

	}
	

	// Top Boundary
	y = CUTTER_Y + ceil(CUTTER_DIAMETER/2) - 1;
	x = CUTTER_X;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Top Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_X + y]);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;
	}
	if(check_correct_cut(x,y+1) == CUTTER_HEIGHT){
		printf("ERROR - Top Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x,y+1));
		return 1;

	}
	if(check_correct_cut(x+3, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Boundary (%d, %d) + 3 is at the wrong height: %d\n", x+3, y, check_correct_cut(x+3, y));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}

	// Bottom Boundary
	x = CUTTER_X;
	y = CUTTER_Y + ceil(CUTTER_DIAMETER/2) - 1;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Bottom Boundary (%d, %d) not the right height: %d\n", x,y, BLOCK[x * DIM_X + y]);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;
	}
	if(check_correct_cut(x,y+1) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x,y+1));
		return 1;

	}
	if(check_correct_cut(x+3, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 3 is at the wrong height: %d\n", check_correct_cut(x+3, y));
		return 3;

	}
	
	// Point on circle x = r * sin theta, y = r * cos theta
	for(float angle = 0; angle < PI/180 * 360; angle += PI/180 * 45){
		printf("Testing angle %f  ...\n", angle / (PI/180));

		x = CUTTER_X + ceil(sin(angle) * CUTTER_DIAMETER/2) - 1;
		y = CUTTER_Y + ceil(cos(angle) * CUTTER_DIAMETER/2) - 1;
		
		if(check_correct_cut(x,y) != CUTTER_HEIGHT){
			printf("ERROR - Angle Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_X + y]);
			print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
			return 1;
		}
		
	}

	
	// TODO: Test to make sure that a value is returned when the block has been cut
	// TODO: Test to make sure a different value is returned when the block has not

	// TODO: Test to make sure the difference (delta) is returned somewhere
	return 0;
}

int main(){
	printf("Starting setup...\n");
	setup();

	printf("Setup done\n");

	

	int output = 0;

	// Test at Normal height
	
	output = test_cut();
	if(BLOCK[5 * DIM_X + 5] != CUTTER_HEIGHT){
		printf("ERROR - block not cut to right height.:%d\n", BLOCK[5 * DIM_X + 5]);
		return 1;
	}
	printf("Output of test_cut: %d\n", output);
	if(output != 0){
		return output;
	}

	setup();

	// Test recutting (or cutting at lower height)

	CUTTER_HEIGHT = 1100;

	test_cut();
	if(BLOCK[50 * DIM_X + 50] == CUTTER_HEIGHT){
		printf("ERROR [Recut] - block not cut to right height.:%d\n", BLOCK[50 * DIM_X + 50]);
		return 1;
	}
	printf("Output of test_cut: %d\n", output);
	if(output != 0){
		return output;
	}
	
	// Test correct circle boundary
	DIM_X = 1000;
	DIM_Y = 1000;
	CUTTER_DIAMETER = 100;  	
	CUTTER_HEIGHT = 750;		

	CUTTER_X = 200;
	CUTTER_Y = 200;

	setup();

	printf("Testing the circle removal...\n");
	output = test_correct_circle_boundary();
	if(output != 0){
		printf("Test failed: %d\n", output);
		return output;
	}
	printf("Output of test correct circle boundary: %d\n", output);
	return 0;
}

