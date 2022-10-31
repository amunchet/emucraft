#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>
#include <string.h>

#define PI 3.14159265

void print_block(int *BLOCK, int DIM_X, int DIM_Y, int start_x, int start_y, int size)
{
	printf("    |\t");
	for (int y = start_y; y < start_y + size; y++)
	{
		printf("%d\t", y);
	}
	printf("\n");

	for (int x = start_x; x < start_x + size; x++)
	{
		for (int y = start_y; y < start_y + size; y++)
		{
			if (y == start_y)
			{
				printf("%d |\t", x);
			}
			if (y < DIM_Y && x < DIM_X && x >= 0 && y >= 0)
			{
				printf(" %d\t", BLOCK[x * DIM_X + y]);
			}
		}

		printf("\n");
	}
}
int check_distance(int x, int y, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER)
{
	int distance_x = abs(x - CUTTER_X);
	int distance_y = abs(y - CUTTER_Y);

	float value = sqrt((distance_x * distance_x) + (distance_y * distance_y));
	float radius = CUTTER_DIAMETER / 2;

	// printf("[check_distance] (%d, %d) with cutter (%d, %d) (d: %d) = %d < %d\n", x, y, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, value , radius);

	if (value < radius)
	{
		return 1;
	}
	return 0;
}

int cut(int *BLOCK, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER, int CUTTER_HEIGHT, int BLOCK_X, int BLOCK_Y, int delta[][3], int *delta_count)
{
	/*
		BLOCK - flat array of block information
		CUTTER_X, CUTTER_Y - where the cutter is
		CUTTER_DIAMETER - ...
		BLOCK_X, BLOCK_Y - dimensions of the block
		delta - an optional argument to pass out 
			- [x][y][z]
	*/

	// Find a block around center of cutter

	int min_x = 0;
	int max_x = 0;

	int min_y = 0;
	int max_y = 0;

	min_x = floor(CUTTER_X - (CUTTER_DIAMETER / 2));
	if (min_x < 0)
	{
		min_x = 0;
	}
	if (min_x > BLOCK_X)
	{
		min_x = BLOCK_X;
	}

	max_x = ceil(CUTTER_X + (CUTTER_DIAMETER / 2));
	if (max_x > BLOCK_X)
	{
		max_x = BLOCK_X;
	}
	if (max_x < 0)
	{
		max_x = 0;
	}

	min_y = floor(CUTTER_Y - (CUTTER_DIAMETER / 2));
	if (min_y < 0)
	{
		min_y = 0;
	}
	if (min_y > BLOCK_Y)
	{
		min_y = BLOCK_Y;
	}

	max_y = ceil(CUTTER_Y + (CUTTER_DIAMETER / 2));
	if (max_y < 0)
	{
		max_y = 0;
	}
	if (max_y > BLOCK_Y)
	{
		max_y = BLOCK_Y;
	}

	// Check for edges

	int total_removed = 0; // This is .001 cubic inches
	int cube_count = 0;

	int difference = 0;

	for (int x = min_x; x < max_x; x++)
	{
		for (int y = min_y; y < max_y; y++)
		{
			if (check_distance(x, y, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER) && BLOCK[x * BLOCK_X + y] > CUTTER_HEIGHT)
			{
				difference = BLOCK[x * BLOCK_X + y] - CUTTER_HEIGHT;
				// printf("[Cut] Difference: %d\n", difference);
				total_removed += difference;
				BLOCK[x * BLOCK_X + y] = CUTTER_HEIGHT;
				cube_count += 1;

				if(delta != NULL){
					printf("In Delta\n");
					delta[*delta_count][0] = x;
					delta[*delta_count][1] = y;
					delta[*delta_count][2] = CUTTER_HEIGHT;
					(*delta_count) = (*delta_count) + 1;
					printf("[DEBUG] Done in delta.\n");
				}

			}
		}
	}

	printf("[Cut] Removed %d number of cubes.\n", cube_count);
	return total_removed;
}

int write_block(int *BLOCK, int DIM_X, int DIM_Y, char *filename)
{
	printf("Writing to %s\n", filename);
	FILE *fp = fopen(filename, "w+");
	for (int x = 0; x < DIM_X; x++)
	{
		for (int y = 0; y < DIM_Y; y++)
		{
			fprintf(fp, "%d ", BLOCK[x * DIM_X + y]);
		}
		fprintf(fp, "\n");
	}
	fclose(fp);
	return 0;
}

int process_from_file(int *BLOCK, int DIM_X, int DIM_Y, char *filename)
{
	/*
	Processes a XYZ File

	Input Format (.xyz file):
		[X] [Y] [Z] [Cutter Diameter] [Tool Holder Diameter] [Tool Holder Z (Bottom)]
		XXXXX XXXXX XXXXX XXXXXX XXXXX XXXXX XXXXX
		6 * (4+1) + 1 * (5) = 35 is the line length

	Output Format (for simulation file - .sim):
		[Step] [X] [Y] [Z value]

	*/

	int x, y, z, cutter_diameter, tool_holder_diameter, tool_holder_z;

	FILE *fp = fopen(filename, "r");
	
	assert(fp != NULL);

	int actual_delta_count = 0;

	char *output_filename;
	sprintf(&output_filename, "%s.sim", filename);
	printf("Output filename: %s\n", output_filename);

	FILE *delta_output = fopen(output_filename, "w");

	int delta_buffer = 512;
	char *delta_lines[delta_buffer];

	int line_count = 0;
	while(EOF != fscanf(fp, "%d %d %d %d %d %d\n", &x, &y, &z, &cutter_diameter, &tool_holder_diameter, &tool_holder_z)){
		printf("%d %d %d %d %d %d\n", x, y, z, cutter_diameter, tool_holder_diameter, tool_holder_z);

		int max_size;
		max_size = ceil(((cutter_diameter / 2) ^ 2) * PI);
		if(max_size < 50){
			max_size = 50;
		}

		int delta[max_size][3];


		cut(BLOCK, x, y, cutter_diameter, z, DIM_X, DIM_Y, delta, &actual_delta_count);

		printf("[DEBUG] Out of cut: %d.\n", actual_delta_count);
		for(int i = 0; i<actual_delta_count; i++){
			printf("Delta: %d (%d, %d, %d)\n", i, delta[i][0], delta[i][1], delta[i][2]);

			sprintf(delta_lines[line_count], "%d %d %d %d\n", line_count, delta[i][0], delta[i][1], delta[i][2]);
			

		}
		// TODO: Second cut for checking
		
		// TODO: Temporary - write delta to file for animation
		if(line_count > 0 && line_count % delta_buffer == 0){
			for(int i = 0; i<delta_buffer; i++){
				fprintf(delta_output, "%s", delta_lines[line_count]);
			}
			line_count = -1;
		}
		line_count++;
	}
	fclose(fp);
	fclose(delta_output);

	
	return 1;
}
