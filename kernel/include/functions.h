#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define PI 3.14159265


void print_block(int* BLOCK, int DIM_X, int DIM_Y, int start_x, int start_y, int size){
	for(int x = start_x; x<start_x+size; x++){
		for(int y = start_y; y<start_y+size; y++){
			if(y < DIM_Y && x < DIM_X && x >= 0 && y >= 0){
				printf(" %d\t", BLOCK[x * DIM_X + y]);
			}
		}
		printf("\n");
	}
}
int check_distance(int x, int y, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER){
	int distance_x = abs(x - CUTTER_X);
	int distance_y = abs(y - CUTTER_Y);

	int value = sqrt((distance_x * distance_x) + (distance_y * distance_y)); 
	int radius = CUTTER_DIAMETER/2;

	//printf("[check_distance] (%d, %d) with cutter (%d, %d) (d: %d) = %d < %d\n", x, y, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, value , radius);

	if(value < radius) {
		return 1;	
	}
	return 0;
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
	
	int total_removed = 0; // This is .001 cubic inches

	for(int x = min_x; x<max_x; x++){
		for(int y = min_y; y<max_y; y++){
			if(check_distance(x, y, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER) && BLOCK[x * BLOCK_X + y] > CUTTER_HEIGHT){
				total_removed += BLOCK[x * BLOCK_X + y] - CUTTER_HEIGHT;
				BLOCK[x * BLOCK_X +y] = CUTTER_HEIGHT;	
			}	
		}
	}
	
	
	return total_removed;
}

int write_block(int* BLOCK, int DIM_X, int DIM_Y, char* filename){
	printf("Writing to %s\n", filename);
	FILE *fp = fopen(filename, "w+");
	for(int x = 0; x<DIM_X; x++){
		for(int y = 0; y<DIM_Y; y++){
			fprintf(fp, "%d ", BLOCK[x * DIM_X + y]);
		}
		fprintf(fp,"\n");
	}
	fclose(fp);
	return 0;
}

int process_from_file(int* BLOCK, int DIM_X, int DIM_Y, char* filename){
	/*
	Processes a XYZ File
	
	Format:
		[X] [Y] [Z] [Cutter Diameter] [Cutter Z value]
	*/

	FILE *fp = fopen(filename, "r");
	int x, y, z, cutter_diameter, cutter_z;
	
	fscanf(fp, "%d %d %d %d %d\n", &x, &y, &z, &cutter_diameter, &cutter_z);
	
	cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT, DIM_X, DIM_Y);
}
