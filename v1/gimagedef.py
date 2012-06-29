
import numpy as np
from scipy import interpolate
from scipy import linalg
import amitgroup as ag
from amitgroup.ml.imagedefw import imagedef, deform, deform_map
from copy import copy
from math import cos 
from itertools import product
PLOT = True 
#PLOT = False
if PLOT: 
    import matplotlib.pylab as plt

def main():
    #import amitgroup.io.mnist
    #images, _ = read('training', '/local/mnist', [9]) 
    images = np.load("data/nines.npz")['images']
    #shifted = np.zeros(images[0].shape)    
    #shifted[:-3,:] = images[0,3:,:]

    #im1, im2 = images[0], shifted
    im1, im2 = images[0], images[2] 

    im1 = ag.io.load_image('data/Images_0', 45)
    im2 = ag.io.load_image('data/Images_1', 23)

    im1 = im1[::-1,:]
    im2 = im2[::-1,:]

    #u = np.zeros((2,3,3))
    #u[:,0,0] = 3.0/twopi/32.0
    #u[0,1,0] = 1.5/twopi/32.0

    # For testing:
    #im2 = deform(im1, u)    

    if 1:
        u = []
        for q in range(2):
            u0 = [np.zeros((1,1))]
            for s in range(0, 20):
                u0.append((np.zeros((2**s,2**s)), np.zeros((2**s,2**s)), np.zeros((2**s,2**s))))
            u.append(u0)

        u[0][1][0][0] += 0.5 
        u[1][1][1][0] += 0.2 

        print u

        xs = np.empty((20, 20, 2))
        for x0 in range(xs.shape[0]):
            for x1 in range(xs.shape[1]):
                xs[x0,x1] = np.array([x0/float(xs.shape[0]), x1/float(xs.shape[1])])
        
        defx = deform_map(xs, u)

        imdef = deform(im1, u)
        d = dict(origin='lower', interpolation='nearest', cmap=plt.cm.gray)

        plt.figure(figsize=(14,4))
        plt.subplot(131)
        plt.title("Original")
        plt.imshow(im1, **d) 
        plt.subplot(132)
        plt.title("Deformed")
        plt.imshow(imdef, **d)
        plt.subplot(133)
        plt.title("Deform map")
        plt.quiver(xs[:,:,1], xs[:,:,0], defx[:,:,1], defx[:,:,0])
        plt.show()
        
    elif 1:
        #import pylab as plt
        #plt.imshow(im2, **d)
        #plt.show()

        u, costs, logpriors, loglikelihoods = imagedef(im1, im2, A=2)
        print u

        if PLOT:
            plt.figure(figsize=(8,12))
            plt.subplot(211)
            plt.semilogy(costs, label="J")
            plt.semilogy(loglikelihoods, label="log likelihood")
            plt.subplot(212) 
            plt.semilogy(logpriors, label="log prior")
            plt.legend()
            plt.show()

    
        if PLOT:
            d = dict(origin='lower', interpolation='nearest', cmap=plt.cm.gray)
            im3 = ag.ml.deform(im1, u)

            plt.figure(figsize=(16,6))
            plt.subplot(141)
            plt.title("Prototype")
            plt.imshow(im1, **d)
            plt.subplot(142)
            plt.title("Original")
            plt.imshow(im2, **d) 
            plt.subplot(143)
            plt.title("Deformed")
            plt.imshow(im3, **d)
            plt.subplot(144)
            plt.title("Deformed")
            plt.imshow(im2-im3, **d)
            plt.show()

    elif 1:
        plt.figure(figsize=(14,6))
        plt.subplot(121)
        plt.title("F")
        plt.imshow(im1, origin='lower')
        plt.subplot(122)
        plt.title("I")
        plt.imshow(im2, origin='lower') 
        plt.show()
    

if __name__ == '__main__':
    import cProfile
    #cProfile.run('main()')
    main()
