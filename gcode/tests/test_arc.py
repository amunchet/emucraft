import math
import os
import arc

def test_find_center():
    """
    Finds center based on I,J,K
    """
    x,y,z = 1,1,1
    i,j,k = 2,2,2

    assert arc.find_center((x,y,z), (i,j,k)) == (3,3,3)

    assert arc.find_length((i,j,k)) == math.sqrt(12) # square of sums of i,j,k

def test_calculate_angle():
    """
    Calculates angle given a circle relative to the vector (x->+, y)
        - This won't actually check the radius, so it doesn't respect whether or not the point is on the circle

    """

    # Given a circle at 1,1,1
    x,y,z = 1,1,1


    # Test the 4 specials - 0,90,180,270
    ## 0 degrees, so 2,1,1
    x2, y2, z2 = 2,1,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 0

    ## 90 degrees, so 1, 2, 1
    x2,y2,z2 = 1,2,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 90

    ## 180 degrees, so -1,1,1
    x2,y2,z2 = -1,1,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 180

    ## 270, so 1,-1,1
    x2,y2,z2 = 1,-1,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 270


    # Check point in each quadrant
    ## 45 degrees
    x2,y2,z2 = 2,2,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 45

    ## 135 degrees
    x2, y2, z2  = 0,2,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 135

    ## 225 degrees

    x2, y2, z2  = 0,0,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 225

    ## 315 degrees
    x2, y2, z2  = 2,0,1
    assert arc.calculate_angle((x,y,z),(x2,y2,z2)) == 315

def test_find_minimum_angle_for_segment():
    """
    Q: Given a radius, what's the angle required to move a length of C (constant)?
    
    Finds the minimum angle for the segment

    Theta = arcsin(Target Constant / R)
    That's the angle to move that minimum distance
    (Probably .001")

    """
    r = 2
    min_length = 1
    assert arc.find_minimum_angle_for_segment(r, min_length) == 30

def test_find_point_from_angle():
    """
    Returns a point given an angle, center coordinates, and radius
    """
    center = (0,0,0)
    radius = 2
    
    angle = 0
    assert arc.calculate_point(center,radius,angle) == (2,0,0)

    angle = 90

    assert arc.calculate_point(center,radius,angle) == (0,2,0)

    angle = 180

    assert arc.calculate_point(center,radius,angle) == (-2,0,0)


    angle = 270
    assert arc.calculate_point(center,radius,angle) == (0,-2,0)

    angle = 360
    assert arc.calculate_point(center,radius,angle) == (2,0,0)


    angle = -90
    assert arc.calculate_point(center,radius,angle) == (0,-2,0)


def test_segment():
    """
    Segments the arc
    """

    # If it's too small, then just return the next point as a linear move
    x = 0
    y = 0
    z = 0

    assert arc.segment(start=(x,y,z), end=(x+1, y+1,z), ijk=(x+5, y+5,0)) == [(x+1,y+1, z)]


    # Need to check both clockwise (G2) and counterclockwise (G3)

    x,y,z = 1,0,0
    assert arc.segment(start=(x,y,z), end=(0, -1, z), ijk=(-1,0,0),g2=True, min_distance=1) == [
        (1,0,z),
        (0,-1,z)
    ]

    assert arc.segment(start=(x,y,z), end=(0, -1, z), ijk=(-1,0,0),g3=True, min_distance=1) == [
        (1,0,z),
        (0,1,z),
        (-1,0,z),
        (0,-1,z)
    ]

