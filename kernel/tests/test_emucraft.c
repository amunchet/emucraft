#include "../src/functions.h"
#include <math.h>
#include <stdio.h>


extern int* BLOCK; // Defined in functions.c


// Sets up the variables
int DIM_X = 10;
int DIM_Y = 10;

int HEIGHT = 1000; // Max is Int16, which is ~30k or 30" in the real world

int CUTTER_DIAMETER = 6;  	
int CUTTER_HEIGHT = 750;		

int CUTTER_X = 5;
int CUTTER_Y = 5;


int check_correct_cut(int x,int y){
	int temp_x;
	int temp_y;

	if(x < 0){
		return -1;
	}else if(x >= DIM_X){
		return -1;
	}else{
		temp_x = x;
	}
	
	if(y < 0){
		return -2;
	}else if(y >= DIM_Y){
		return -2;
	}else{
		temp_y = y;
	}
	printf("[check_correct_cut] Value of (%d, %d) is %d\n", x, y, BLOCK[temp_x * DIM_Y + temp_y]);
	
	return BLOCK[temp_x * DIM_Y + temp_y];
}
int test_setup(){

	BLOCK = malloc ((DIM_X * DIM_Y) * sizeof(int));
	for (int i=0; i<DIM_X * DIM_Y; i++){
		BLOCK[i] = HEIGHT;

	}
	// Print 10 x 10 block
	print_block(BLOCK, DIM_X, DIM_Y, 0,0, 10);

	if(check_correct_cut(5,5) != HEIGHT){
		printf("ERROR - block not initialized properly: %d\n", check_correct_cut(5,5));
		return 1;
	}
	return 0;
}

int test_cut(int expected){
	// Tests cutting the structure (removing a circle)
	
	printf("Starting up test_cut...");

	
	int success = cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT, DIM_X, DIM_Y, NULL, 0);
	if(success != expected){
		printf("ERROR - block not cut properly: %d.  Expected: %d\n", success, expected);
		return 1;

	}
	return 0;
}



int test_correct_circle_boundary(){
	// Test that only within the circle is cut
	if(check_correct_cut(5,5) != HEIGHT){
		printf("ERROR - block not initialized properly.");
		return 1;
	}


	int output = cut(BLOCK, CUTTER_X, CUTTER_Y, CUTTER_DIAMETER, CUTTER_HEIGHT, DIM_X, DIM_Y, NULL, 0);
	if(output == 0){
		printf("ERROR - no cut was made\n");
		return 1;
	}
	printf("Cut returned: %d\n", output);

	
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

	// Although the theoretical edge is at CUTTER_Y, due to rounding, the circle actuallys stays on CUTTER_Y for a ways, depending on the radius
	/*
	int y_amount = 1;
	if(check_correct_cut(x,y+y_amount) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + y_amount (%d, %d) is at the wrong height: %d\n", x, y+y_amount, check_correct_cut(x,y+y_amount));
		
		printf("Block:%d, %d\n", x, y+y_amount);
		write_block(BLOCK, DIM_X, DIM_Y, "broken.block");
		print_block(BLOCK, DIM_X, DIM_Y, x, y+y_amount-30, 10);
		return 1;

	}
	*/
	if(check_correct_cut(x-1, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary - 1 (%d, %d) is at the wrong height: %d\n", x-1, y, check_correct_cut(x-1, y));
		print_block(BLOCK, DIM_X, DIM_Y, x-1, y, 10);
		return 1;

	}
	
	// Right Boundary
	x = CUTTER_X + ceil(CUTTER_DIAMETER/2) - 1;
	y = CUTTER_Y;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Top Right Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_Y + y]);
		return 1;
	}
	// Same as the Top Left
	/*
	if(check_correct_cut(x,y+3) == CUTTER_HEIGHT){
		printf("ERROR - Top Right Boundary + 3 is at the wrong height: %d\n", check_correct_cut(x,y+3));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}*/

	if(check_correct_cut(x+1, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x+1, y));
		return 1;

	}
	

	// Top Boundary
	y = CUTTER_Y + ceil(CUTTER_DIAMETER/2) - 1;
	x = CUTTER_X;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Top Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_Y + y]);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;
	}
	if(check_correct_cut(x,y+1) == CUTTER_HEIGHT){
		printf("ERROR - Top Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x,y+1));
		return 1;

	}
	/* Same as the others, just for X instead of Y */
	/*
	if(check_correct_cut(x+3, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Boundary (%d, %d) + 3 is at the wrong height: %d\n", x+3, y, check_correct_cut(x+3, y));
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;

	}
	*/

	// Bottom Boundary
	x = CUTTER_X;
	y = CUTTER_Y + ceil(CUTTER_DIAMETER/2) - 1;

	if(check_correct_cut(x,y) != CUTTER_HEIGHT){
		printf("ERROR - Bottom Boundary (%d, %d) not the right height: %d\n", x,y, BLOCK[x * DIM_Y + y]);
		print_block(BLOCK, DIM_X, DIM_Y, 0, 0, 10);
		return 1;
	}
	if(check_correct_cut(x,y+1) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 1 is at the wrong height: %d\n", check_correct_cut(x,y+1));
		return 1;

	}
	// Same as the others, just for X instead of Y
	/* 
	if(check_correct_cut(x+3, y) == CUTTER_HEIGHT){
		printf("ERROR - Top Left Boundary + 3 is at the wrong height: %d\n", check_correct_cut(x+3, y));
		return 3;

	} */
	
	// Point on circle x = r * sin theta, y = r * cos theta
	for(float angle = 0; angle < PI/180 * 360; angle += PI/180 * 45){
		float calculated = angle / (PI/180);
		printf("Testing angle %f...\n", calculated );
		
	
		x = ceil(CUTTER_X + (sin(angle) * CUTTER_DIAMETER/2));
		y = ceil(CUTTER_Y + (cos(angle) * CUTTER_DIAMETER/2));
	
		if (calculated < 46){
			y -= 2; // Rounding
		}else if(calculated > 46 && calculated < 135){
			x -= 1;
		}else if(calculated >= 135 && calculated < 180){
			y += 1;
		}else if(calculated < 359){
			x += 1;
		}else{
			y -= 1;
		}
		
		if(check_correct_cut(x,y) != CUTTER_HEIGHT){
			printf("ERROR - Angle Boundary (%d, %d) not the right height: %d\n", x, y, BLOCK[x * DIM_Y + y]);
			print_block(BLOCK, DIM_X, DIM_Y, x-5, y-5, 10);
			return 1;
		}
		
	}

	
	return 0;
}

int test_non_square(int dim_x, int dim_y, int cutter_x, int cutter_y){
	/*
		Cuts a non-square block and checks every cell: inside the cutter is cut,
		everything else is untouched.  Cuts near the far corner so bad indexing
		either lands on the wrong cell or runs off the end of the block.
	*/
	printf("Testing non-square block %d x %d, cut at (%d, %d)...\n", dim_x, dim_y, cutter_x, cutter_y);
	int diameter = 6;
	int height = 1000;
	int depth = 750;

	int *block = malloc((dim_x * dim_y) * sizeof(int));
	for (int i=0; i<dim_x * dim_y; i++){
		block[i] = height;
	}

	int removed = cut(block, cutter_x, cutter_y, diameter, depth, dim_x, dim_y, NULL, 0);

	int expected_cells = 0;
	for(int x = 0; x < dim_x; x++){
		for(int y = 0; y < dim_y; y++){
			int expected = check_distance(x, y, cutter_x, cutter_y, diameter) ? depth : height;
			if(expected == depth){
				expected_cells++;
			}
			if(block[x * dim_y + y] != expected){
				printf("ERROR [Non-square] - (%d, %d) is %d, expected %d\n", x, y, block[x * dim_y + y], expected);
				free(block);
				return 1;
			}
		}
	}

	if(expected_cells == 0 || removed != expected_cells * (height - depth)){
		printf("ERROR [Non-square] - removed %d, expected %d\n", removed, expected_cells * (height - depth));
		free(block);
		return 1;
	}

	free(block);
	return 0;
}

int main(){
	printf("Starting setup...\n");
	test_setup();

	printf("Setup done\n");

	

	int output = 0;

	// Test at Normal height
	
	output = test_cut(6250); // 6250 is the amount of material removed
	if(BLOCK[5 * DIM_Y + 5] != CUTTER_HEIGHT){
		printf("ERROR - block not cut to right height.:%d\n", BLOCK[5 * DIM_Y + 5]);
		return 1;
	}
	printf("Output of test_cut: %d\n", output);
	if(output != 0){
		return output;
	}
	
	free(BLOCK);
	test_setup();

	// Test recutting (or cutting at lower height)

	CUTTER_HEIGHT = 1100;

	output = test_cut(0); // Expect 0 on an air cut
	if(BLOCK[5 * DIM_Y + 5] != HEIGHT){
		printf("ERROR [Recut] - air cut changed the block:%d\n", BLOCK[5 * DIM_Y + 5]);
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
	
	HEIGHT = 2000;

	free(BLOCK);
	test_setup();

	printf("Testing the circle removal...\n");
	output = test_correct_circle_boundary();
	if(output != 0){
		printf("Test failed: %d\n", output);
		return output;
	}
	printf("Output of test correct circle boundary: %d\n", output);

	// Non-square blocks, both orientations
	if(test_non_square(20, 50, 17, 46) != 0){
		return 1;
	}
	if(test_non_square(50, 20, 46, 17) != 0){
		return 1;
	}

	printf("All tests passed\n");
	return 0;
}

