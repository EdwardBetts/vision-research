
import numpy as np


def get_key_points(weights, suppress_radius=2, max_indices=np.inf, even=False): 
    indices = []
    #kern = detector.kernel_templates[k][m]
    #bkg = detector.fixed_spread_bkg[k][m]
    #eps = detector.settings['min_probability']
    #kern = np.clip(kern, eps, 1 - eps)
    #bkg = np.clip(bkg, eps, 1 - eps)
    #weights = np.log(kern / (1 - kern) * ((1 - bkg) / bkg))

    F = weights.shape[-1]

    rs = np.random.RandomState(0)

    # Add some noise, since we want each feature to have the same amount of features,
    # we must perturb the weights a bit since everyone is relying on the minimum amount.
    ww = weights
    absw_pos = np.maximum(0, ww)# + rs.normal(0, 0.00000001, size=weights.shape)
    absw_neg = -np.minimum(0, ww)# + rs.normal(0, 0.00000001, size=weights.shape)

    import scipy.stats
    #almost_zero = scipy.stats.scoreatpercentile(np.fabs(weights.ravel()), 20)
    almost_zero = 0

    indices_weights = []

    #supp = detector.settings.get('indices_suppress_radius', 4)
    for absw in [absw_pos, absw_neg]:
        while True: 
            if absw.max() <= almost_zero:
                break
            ii = np.unravel_index(np.argmax(absw), absw.shape)
            indices.append(ii) 
            indices_weights.append(absw[ii])
            absw[max(0, ii[0]-suppress_radius):ii[0]+suppress_radius+1, max(0, ii[1]-suppress_radius):ii[1]+suppress_radius+1,ii[2]] = 0.0

            if len(indices) >= max_indices:
                break

    # Sort indices by weight
    indices = np.asarray(indices, dtype=np.int32)
    II = np.argsort(indices_weights)[::-1]
    indices = indices[II]

    Ls = np.bincount(indices[:,2], minlength=weights.shape[-1])
    L = int(np.median(Ls))

    print 'indices per features:', L

    if not even:
        return indices
    #new_indices = []
    else: 
        new_indices = np.zeros((L * F, 3), dtype=np.int32)

        for f in xrange(F):
            indf = indices[indices[...,2] == f]

            new_indices[L*f:L*f + indf.shape[0]] = indf

            # Fill up the rest with random indices that are not taken
            for l in xrange(int(indf.shape[0]), L):
                while True:
                    l0, l1 = rs.randint(weights.shape[0]), rs.randint(weights.shape[1])

                    # Check if it's in there already
                    if not (indices == [l0, l1, f]).all(1).any():
                        new_indices[L*f + l] = (l0, l1, f)
                        break

        return new_indices
    if 0:
        Ls = np.bincount(indices[:,2], minlength=weights.shape[-1])
        min_L = np.min(Ls)
        #print min_L, np.max(Ls)
        #print indices_weights
        new_indices = []
        bins = np.zeros(weights.shape[-1])
        for index in indices:
            if bins[index[2]] < min_L:
                new_indices.append(index)
                bins[index[2]] += 1

        new_indices = np.array(new_indices, dtype=np.int32)

        # Sort
        new_indices = new_indices[np.argsort(new_indices[:,2])] 

        return new_indices
        
