#include "../src/functions.h"
#include <math.h>
#include <stdio.h>

// Non-square on purpose, so row/column mix-ups show up
int DIM_X = 10;
int DIM_Y = 15;

int HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world

extern int* BLOCK; // Defined in functions.c



int test_setup(){
	printf("Starting setup...\n");
	BLOCK = malloc ((DIM_X * DIM_Y) * sizeof(int));
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = HEIGHT;

	}
	// Print 10 x 10 block
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);
	printf("Done\n");
	return 0;
}

int write_xyz(char *filename, char *contents){
	FILE *fp = fopen(filename, "w");
	if(fp == NULL){
		return 1;
	}
	fputs(contents, fp);
	fclose(fp);
	remove("tmp_test.xyz.sim");
	return 0;
}

int expect_height(int x, int y, int expected, char *name){
	if(BLOCK[x * DIM_Y + y] != expected){
		printf("ERROR [%s] - (%d, %d) is %d, expected %d\n", name, x, y, BLOCK[x * DIM_Y + y], expected);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 15);
		return 1;
	}
	return 0;
}

int test_read_in_file(){
	// Tests reading in a processed file (not G-code, the XYZ file plus cutting information)
	printf("Block before file...\n");
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);

	// 7 column format from the G-code parser, including a non-cutting move
	write_xyz("tmp_test.xyz",
		"2 2 900 3 7 10 0\n"
		"2 12 800 3 7 10 1\n");

	if(process_from_file(BLOCK, DIM_X, DIM_Y, "tmp_test.xyz") != 1){
		printf("ERROR - process_from_file failed on 7 column file\n");
		return 1;
	}

	printf("Block after file...\n");
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 15);

	if(expect_height(2, 2, 900, "7 column, first line")) return 1;
	if(expect_height(2, 12, 800, "7 column, second line")) return 1;
	if(expect_height(7, 7, HEIGHT, "7 column, untouched")) return 1;

	// Older 6 column format still works, and blank lines are skipped
	write_xyz("tmp_test.xyz",
		"7 7 700 3 7 10\n"
		"\n"
		"7 13 600 3 7 10\n");

	if(process_from_file(BLOCK, DIM_X, DIM_Y, "tmp_test.xyz") != 1){
		printf("ERROR - process_from_file failed on 6 column file\n");
		return 1;
	}
	if(expect_height(7, 7, 700, "6 column, first line")) return 1;
	if(expect_height(7, 13, 600, "6 column, second line")) return 1;

	// Missing file is an error, not a crash
	if(process_from_file(BLOCK, DIM_X, DIM_Y, "does_not_exist.xyz") != -1){
		printf("ERROR - missing file did not return -1\n");
		return 1;
	}

	remove("tmp_test.xyz");
	remove("tmp_test.xyz.sim");
	return 0;
}

int test_write_file(){
	// Tests writing out to a file
	printf("Starting write_file test...\n");
	BLOCK[0] = 750;
	BLOCK[5 * DIM_Y + 1] = 750;
	BLOCK[(DIM_X - 1) * DIM_Y + (DIM_Y - 1)] = 500;
	int output = write_block(BLOCK, DIM_X, DIM_Y, "tmp_test.block");

	if(output != 0){
		printf("Write Block failed\n");
		return 1;
	}
	printf("Done with writing block.\n");
	FILE *fp = fopen("tmp_test.block", "r");

	printf("Opening tmp_test.block...\n");

	for(int x = 0; x < DIM_X; x++){
		for(int y = 0; y < DIM_Y; y++){
			int value;
			if(fscanf(fp, "%d", &value) != 1){
				printf("Error - tmp_test.block ended early at (%d, %d)\n", x, y);
				fclose(fp);
				return 1;
			}
			if(value != BLOCK[x * DIM_Y + y]){
				printf("Error - Block and read block don't match at (%d, %d): ", x, y);
				printf("%d vs %d\n", value, BLOCK[x * DIM_Y + y]);
				fclose(fp);
				return 1;
			}
		}
	}
	fclose(fp);
	remove("tmp_test.block");

	// Reset for the next test
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = HEIGHT;
	}
	printf("Completed\n");
	return 0;
}

int main(){
	int output = test_setup();
	if(output != 0){
		printf("Setup failed\n");
		return output;
	}
	printf("Starting write file...\n");
	output = test_write_file();
	if(output != 0){
		printf("Test Write File failed.\n");
		return output;
	}
	printf("Starting read file...\n");
	output = test_read_in_file();
	if(output != 0){
		printf("Test Read File failed.\n");
		return output;
	}
	printf("All tests passed\n");
	return 0;
}
