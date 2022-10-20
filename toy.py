import trimesh
import math
import time

start = time.perf_counter()

scene = trimesh.scene.scene.Scene()

for i in range(0,100_000):
    # a = trimesh.creation.box((1+i,2+i,3+i))
    b = trimesh.creation.box((5,5,5))

    move_matrix = trimesh.transformations.translation_matrix([i*5,0,0])
    b.apply_transform(move_matrix)

    # scene.add_geometry(a)
    scene.add_geometry(b)


scene.export("toy.stl")
print(time.perf_counter() - start, "seconds")