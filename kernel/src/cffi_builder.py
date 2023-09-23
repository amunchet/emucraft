from cffi import FFI
ffibuilder = FFI()

# main.c functions
ffibuilder.cdef("int setup(int DIM_X, int DIM_Y, int HEIGHT);")
#ffibuilder.cdef("int main(int argc, char **argv);")

# functions.h functions
ffibuilder.cdef("void print_block(int *BLOCK, int DIM_X, int DIM_Y, int start_x, int start_y, int size);")
ffibuilder.cdef("void print_global_block(int DIM_X, int DIM_Y, int start_x, int start_y, int size);")
ffibuilder.cdef("int check_distance(int x, int y, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER);")
ffibuilder.cdef("int cut(int *BLOCK, int CUTTER_X, int CUTTER_Y, int CUTTER_DIAMETER, int CUTTER_HEIGHT, int BLOCK_X, int BLOCK_Y, char *delta_filename, int delta_count);")
ffibuilder.cdef("int write_block(int *BLOCK, int DIM_X, int DIM_Y, char *filename);")
ffibuilder.cdef("int process_from_file(int *BLOCK, int DIM_X, int DIM_Y, char *filename);")


ffibuilder.set_source("_emukernel",  # name of the output C extension
"""
    #include "functions.h"
""",
    sources=['main.c', 'functions.c'],   
    libraries=['m'])    # on Unix, link with the math library

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)