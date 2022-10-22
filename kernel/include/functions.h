#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define PI 3.14159265


void print_block(int* BLOCK, int DIM_X, int DIM_Y){
	for(int x = 0; x<DIM_X; x++){
		for(int y = 0; y<DIM_Y; y++){
			printf("%d ", BLOCK[x * DIM_X + y]);
		}
		printf("\n");
	}
}
bool check_distance(int x, int y, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER){
	int distance_x = abs(x - CUTTER_X);
	int distance_y = abs(y - CUTTER_Y);

	if(sqrt((distance_x * distance_x) + (distance_y * distance_y)) < CUTTER_DIAMETER/2){
		return true;	
	}
	return false;
}

int cut(int* BLOCK, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER, int CUTTER_HEIGHT, int BLOCK_X, int BLOCK_Y){
	
	// Find a block around center of cutter
	
	int min_x = 0;
	int max_x = 0;
	
	int min_y = 0;
	int max_y = 0;

	min_x = floor(CUTTER_X - (CUTTER_DIAMETER/2));
	if (min_x < 0){
		min_x = 0;
	}
	if (min_x > BLOCK_X){
		min_x = BLOCK_X;
	}
	
	max_x = ceil(CUTTER_X + (CUTTER_DIAMETER/2));
	if (max_x > BLOCK_X){
		max_x = BLOCK_X;
	}
	if(max_x < 0){
		max_x = 0;
	}

	min_y = floor(CUTTER_Y - (CUTTER_DIAMETER/2));
	if(min_y < 0){
		min_y = 0;
	}
	if(min_y > BLOCK_Y){
		min_y = BLOCK_Y;
	}
	
	max_y = ceil(CUTTER_Y + (CUTTER_DIAMETER/2));
	if(max_y < 0){
		max_y = 0;
	}
	if(max_y > BLOCK_Y){
		max_y = BLOCK_Y;
	}
	
	// Check for edges
	for(int x = min_x; x<max_x; x++){
		for(int y = min_y; y<max_y; y++){
			if(check_distance(x, y, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER) && BLOCK[x * BLOCK_X + y] > CUTTER_HEIGHT){
				BLOCK[x * BLOCK_X +y] = CUTTER_HEIGHT;	
			}	
		}
	}
	
	
	return 0;
}
