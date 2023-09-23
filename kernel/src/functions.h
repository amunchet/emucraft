#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>
#include <string.h>

#define PI 3.14159265

int setup(int DIM_X, int DIM_Y, int HEIGHT);

void print_block(int *BLOCK, int DIM_X, int DIM_Y, int start_x, int start_y, int size);
void print_global_block(int DIM_X, int DIM_Y, int start_x, int start_y, int size);

int check_distance(int x, int y, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER);

int cut(int *BLOCK, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER, int CUTTER_HEIGHT, int BLOCK_X, int BLOCK_Y, char *delta_filename, int delta_count);

int write_block(int *BLOCK, int DIM_X, int DIM_Y, char *filename);

int process_from_file(int *BLOCK, int DIM_X, int DIM_Y, char *filename);

