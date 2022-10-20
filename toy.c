#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int main(){
        srand(time(NULL));

        int DIM_X = 10000;
        int DIM_Y = 10000;

        int* a = malloc((DIM_X * DIM_Y) * sizeof(int));


        for(int x=0; x<DIM_X; x++){
                for(int y=0; y<DIM_Y; y++){
                        a[x * DIM_X + y] = 515;
                }
        }

        printf("%d\n", a[1000 * DIM_X + 1000]);

        for(int x=1000; x<5000; x++){
                for(int y=500; y<3000; y++){
                        a[x * DIM_X + y] -= 100;
                }
        }
        printf("%d\n", a[1000 * DIM_X + 1000]);
        return 1;
}