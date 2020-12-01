import numpy as np
import cv2
import dlib
import math
import sys
import pickle
import argparse
import os
import skvideo.io
import random

def get_ellipse_parameters(l, t, r, b):
    center = np.array([1.0*(l[0]+r[0])/2, 1.0*(l[1]+r[1])/2])
    major_axis_vector = np.array([1.0*r[0]-l[0], 1.0*r[1]-l[1]])
    minor_axis_vector = np.array([1.0*t[0]-b[0], 1.0*t[1]-b[1]])
    major_axis_length = int(np.sqrt(np.dot(major_axis_vector, major_axis_vector))/1.4 + np.random.normal(0, 0.13,1))
    minor_axis_length = int(np.sqrt(np.dot(minor_axis_vector, minor_axis_vector))/1.1 + np.random.normal(0, 0.13,1))
    center = center - minor_axis_vector / 4
    e_x = np.array([1,0])
    angle =  -180 / np.pi *np.arccos(np.dot(major_axis_vector, e_x)/(np.sqrt(np.dot(major_axis_vector, major_axis_vector)))) #+ np.random.normal(0, 7, 1)
    if l[1] < r[1]:
        angle *= -1
    return (int(center[0]), int(center[1])), (major_axis_length, minor_axis_length), angle

def draw_lips(image, lip_features, offset):
    lip_features = (lip_features + offset).astype(int)
    print(lip_features)

    for i in range(0, 12):
        cv2.line(image, (lip_features[i%12,0], lip_features[i%12,1]), (lip_features[(i+1)%12,0], lip_features[(i+1)%12,1]), (255,255,255))
    for i in range(0,8):
        cv2.line(image, (lip_features[i%8 + 12,0],lip_features[i%8 + 12,1]), (lip_features[(i+1)%8 + 12,0],lip_features[(i+1)%8 + 12,1]), (255,255,255))

def make_lip_image(lip_features):
    # Rotate and Scale
    dst_lip_features = lip_features
    original_axis = np.array([1.0,0.0])
    dst_axis = dst_lip_features[6,:] - dst_lip_features[0,:]
    cosine = np.dot(dst_axis, original_axis)/np.sqrt(np.dot(dst_axis, dst_axis))/np.sqrt(np.dot(original_axis, original_axis))
    cosine = max(min(cosine, 1.0), -1.0) # Floating point error
    angle = np.arccos(cosine)
    if dst_axis[1] > original_axis[1]:
        angle = angle*-1
    rotation_matrix = np.array([[np.cos(angle), -np.sin(angle)],[np.sin(angle), np.cos(angle)]]) 
    mouth_center = (dst_lip_features[14,:] + dst_lip_features[18,:])/2

    dst_lip_features = dst_lip_features - mouth_center
    dst_lip_features = np.matmul(rotation_matrix, dst_lip_features.T)
    dst_lip_features = dst_lip_features.T + mouth_center

    left = float('inf')
    top = float('inf')
    right = 0
    bottom = 0

    for i in range(lip_features.shape[0]):
        left = min(left, lip_features[i, 0])
        top = min(top, lip_features[i, 1])
        right = max(right, lip_features[i,0])
        bottom = max(bottom, lip_features[i,1])

    translate = np.array([left, top]).T
    dst_lip_features = dst_lip_features - translate

    lip_width = right - left 
    lip_height = bottom - top 

    if lip_width > lip_height:
        offset = (lip_width - lip_height) / 2
        translate = np.array([0, offset]).T
        dst_lip_features = dst_lip_features + translate 
    else:
        offset = (lip_height - lip_width) / 2
        translate = np.array([offset, 0]).T
        dst_lip_features = dst_lip_features + translate  

    dst_lip_features = dst_lip_features.astype(int)
    lip_outline_image = np.zeros((max(lip_width, lip_height), max(lip_width, lip_height), 3))
    cv2.fillPoly(lip_outline_image,[dst_lip_features[np.r_[0:7,16,15,14,13,12]]],(255,0,0))
    cv2.fillPoly(lip_outline_image,[dst_lip_features[np.r_[12:20]]],(0,255,0))
    cv2.fillPoly(lip_outline_image,[dst_lip_features[np.r_[6:12,0, 12, 19,18,17,16]]],(0,0,255))
    return lip_outline_image

def get_crop_bounds(face, features):
    leftmost_face_feature = float('Inf')
    rightmost_face_feature = -1
    lowermost_face_feature = -1
    topmost_face_feature = float('Inf')
    for i in range(1, 18):
        feature = features.part(i)
        leftmost_face_feature = min(leftmost_face_feature, feature.x)
        rightmost_face_feature = max(rightmost_face_feature, feature.x)
        lowermost_face_feature = max(lowermost_face_feature, feature.y)
        topmost_face_feature = min(topmost_face_feature, feature.y)

    width = rightmost_face_feature - leftmost_face_feature
    height = lowermost_face_feature - topmost_face_feature
    if width < height:
        leftmost_face_feature -= (height - width) / 2
        rightmost_face_feature = leftmost_face_feature + height
    elif width > height:
        topmost_face_feature -= (width - height) / 2
        lowermost_face_feature = topmost_face_feature + width
    return leftmost_face_feature, rightmost_face_feature, topmost_face_feature, lowermost_face_feature

def blackout_background(image, face_features):
    background_mask = np.zeros((13,2))
    for i in range(2,15):
        jaw_point = np.array([face_features[i,0], face_features[i,1]])
        background_mask[i-2,:] = jaw_point
    background_mask = (background_mask.reshape((-1,1,2))).astype(int)
    stencil = np.zeros(image.shape).astype(image.dtype)
    cv2.fillPoly(stencil, [background_mask], [255,255,255])
    result = cv2.bitwise_and(image, stencil)
    return result

def blackout_jaw(image, face_features, inv=False):
    face_mask = np.zeros((12,2))
    mouth_center = np.array([face_features[62,0], face_features[62,1]])
    for i in range(4, 13):
        jaw_point = np.array([face_features[i,0], face_features[i,1]])
        vec = mouth_center-jaw_point
        vec = vec/np.linalg.norm(vec)* (10 + np.random.normal(0,5))
        jaw_point = jaw_point+vec 
        face_mask[i-4,:] = jaw_point
    under_nose_point = np.array([face_features[33,0], face_features[33,1]])
    vec = mouth_center-under_nose_point
    vec = (vec/np.linalg.norm(vec)*5).astype(int)
    right_top_point = np.array([face_features[13,0], face_features[13,1]]) 
    left_top_point = np.array([face_features[3,0], face_features[3,1]]) 
    face_mask[9,:] = (under_nose_point+right_top_point)/2
    face_mask[11,:] = (under_nose_point+left_top_point)/2
    under_nose_point+=vec 
    face_mask[10,:] = under_nose_point
      
    face_mask = (face_mask.reshape((-1,1,2))).astype(int)
    if inv:
        stencil = np.zeros(image.shape).astype(image.dtype)
        cv2.fillPoly(stencil, [face_mask], (255,255,255))
        return cv2.bitwise_and(image, stencil)
    else:
        cv2.fillPoly(image,[face_mask],(255,0,255))

######################==== Redblock - preprocessing - Working Directory Set #1 ====###########################
# Parameters, (Working Directory Set)
video_path = '/content/lip2lip_Research/preprocessing/src_video.mp4'
# source video path
train_dir = '/content/lip2lip_Research/data/data_1/train/'
# Path to the folder where train data is kept
num_test_images = 1000 # num of test images

##############################################################################################################



######################==== Redblock - preprocessing - Load face detector #2 ====###########################
# Load face detector (Load face detector)
detector = dlib.get_frontal_face_detector()
# face detector ,, return type (dlib.fhog_object_detector)
predictor = dlib.shape_predictor('/content/lip2lip_Research/preprocessing/shape_predictor_68_face_landmarks.dat')
# extraction face feature point ,, return type (dlib.shape_predictor)
###########################################################################################################


######################==== Redblock - preprocessing - Load video shape #3 ====###########################
# Load video and setup (Load video shpae)
reader = skvideo.io.FFmpegReader(video_path) #Load Video
"""
FFmepg is a tool that replaces video with frame.
No input or input = 0, FPS = Full.
"""
#print(type(reader))
#############################################################################################################


######################==== Redblock - preprocessing - Viedo shape setup #4 ====###########################
# Load video and setup (Video shape setup)
video_shape = reader.getShape()
# shape of video (Setup video #1)
(num_frames, h, w, c) = video_shape
# video shape = (num of frames, height of frame, width of frame, channel of frame) (Setup video #2)
frame_count = 0 #(Setup video #3)
# frame count initialized
print('Number of frames ' + str(num_frames))
"""
#User friendly
print("Height of frame : "+str(h))
print("Width of frame : "+str(w))
print("Channel of frame : "+str(c))
print(type(video_shape))
"""
##########################################################################################################

for frame in reader.nextFrame():
    if frame_count >= num_test_images: # when excution number is larger than test img num
        break
    if frame_count % 10 == 0: # when frame count is 10 times 
        print('On frame ' + str(frame_count) + ' of ' + str(num_frames)) # print portion of excuted frame
    face_image = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # face image translation RGB to BRG
    faces = detector(frame, 1) # face detection of frame using oversampling

    if len(faces) > 1:
        print('DETECTED MORE THAN ONE FACE')
        continue
    if len(faces) == 0:
        print('DETECTED NO FACES')
        continue

    # Extract face features(Face features extraction)
    principal_face = faces[0] # principal face 
    shape = predictor(frame, principal_face) # extraction face feature point in principal face

    # Get Crop Bounds (Get about a cut boundary)
    leftmost_face_feature, rightmost_face_feature, topmost_face_feature, lowermost_face_feature = get_crop_bounds(principal_face, shape)

    # Save face features in np array (Save face features)
    lip_features = np.zeros((20,2)) # allocation lip features matrix
    face_features = [] 
    face_features = np.zeros((68, 2)) # allocation face features matrix
    for i in range(0, 68):
        feature = shape.part(i) # face feature extraction
        face_features[i,:] = np.array([feature.x, feature.y]) # init face feature matrix
    face_features = face_features.astype(int) # dace feature data type change to integer
    lip_features = face_features[48:,:] #  init lip feature matrix

    # Blackout jaw and background (Blackout jaw and background)
    face_image_annotated = np.copy(face_image) # face image copy
    blackout_jaw(face_image_annotated, face_features) # blackout jaw image
    face_image_annotated = blackout_background(face_image_annotated, face_features) # blackout background image
    face_image = blackout_background(face_image, face_features) # blackout background image

    # Create lip outline image (Create lip outline image)
    lips_outline_image = make_lip_image(lip_features)
    
    # Crop and Scale
    #(Image Cropping)
    face_image = face_image[int(topmost_face_feature):int(lowermost_face_feature),int(leftmost_face_feature):int(rightmost_face_feature)] # crop face image
    face_image_annotated = face_image_annotated[int(topmost_face_feature):int(lowermost_face_feature),int(leftmost_face_feature):int(rightmost_face_feature)] # crop face image

    #(Image scaling)
    face_image = cv2.resize(face_image, (256,256)) # resize face image
    face_image_annotated = cv2.resize(face_image_annotated, (256,256)) # resize face image
    lips_outline_image = cv2.resize(lips_outline_image, (256,256)) # resize outline image

    # Stack and save
    stacked = np.concatenate((face_image, face_image_annotated, lips_outline_image), axis = 1) # synthesis face image, face image annotated and lips outline image
    cv2.imwrite(train_dir + str(frame_count) + '.png', stacked) # save synthesis image

    frame_count += 1 # frame count +1

print("Complete Train, Done..")
