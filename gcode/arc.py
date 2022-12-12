#!/usr/bin/env python3
"""
Arc Utilities
    - Used to segment a circle into discrete points
"""
import math

def find_center(coords, center):
    """
    Finds the center of the circle, based on I,J,K

    NOTE: This may change if we have different implementations of the G-Code (such as one which uses absolute I,J,K coordinates)
    """
    (x,y,z) = coords
    (i,j,k) = center
    return (x+i, y+j, k+z)

def find_length(center):
    """
    Finds the radius of the circle (i.e. distance to I,J,K)
    """

    (i,j,k) = center
    return math.sqrt((i ** 2) + (j ** 2) + (k ** 2))

def calculate_angle(center, coords):
    """
    Calculates a the angle of a given point to the center of the circle

    Returns the angle in degrees
    """
    (x,y,z) = center
    (i,j,k) = coords

    answer = math.atan2(j-y, i-x)
    answer_degrees = int(math.degrees(answer))


    if answer_degrees < 0:
        return 360 + answer_degrees

    return answer_degrees

def find_minimum_angle_for_segment(r, min_length):
    """
    Finds the minimum angle to move a given distance
    """
    angle = math.asin(min_length/r)
    return int(math.degrees(angle))

def calculate_point(center, radius, angle):
    x,y,z = center
    angle_rad = math.radians(angle)

    print("[Calcualte Points] center:", center, " and radius:", radius)

    point_x = int(round(x + (radius * math.cos(angle_rad)), 0))
    point_y = int(round(y + (radius * math.sin(angle_rad)), 0))

    print("[Calculate Point] x:", point_x, ", y:", point_y, ", z:", z)

    return (point_x, point_y, z)

def segment(start, end, ijk, min_distance=1, g2=True, g3=False):
    """

    - Find the minimum step angle
    - Divide up the difference between the 2 angles
    - Return the points list

    """

    (start_x, start_y, start_z) = start
    (end_x, end_y, end_z) = end

    center = find_center(start, ijk)
    (center_x, center_y, center_z) = center
    radius = find_length(ijk)
    
    angle_step = find_minimum_angle_for_segment(radius, min_distance)

    start_angle = calculate_angle(center, start)

    end_angle = calculate_angle(center,end)

    if start_angle == end_angle:
        return [end]

    # TODO: Check if G2/G3 (Clockwise, Counter)

    if not g2 and not g3:
        raise Exception ("Cannot have both G2 and G3 false")

    if g2 and g3:
        raise Exception("Cannot have both G2 and G3 set to true")
    
    if g2 and start_angle > end_angle:
        begin = end_angle
        end = start_angle
    
    if g2 and start_angle < end_angle:
        begin = start_angle
        end = end_angle + 360
    
    if g3 and start_angle < end_angle:
        begin = end_angle
        end = start_angle
    
    if g3 and start_angle > end_angle:
        being = start_angle
        end = end_angle + 360

    if angle_step == 0:
        angle_step = 1

    print("Begin:", begin)
    print("End:",end)
    print("Angle step:", angle_step)


    print()

    print("center:", center)
    print("Radius:", radius)
    print()

    points = []
    for i in range(begin, end, angle_step):
        print("Loop Angle:", i)
        points.append(calculate_point(center, radius, i))
    

    print()
    return set(points)

