
import amitgroup as ag
import numpy as np
import matplotlib.pylab as plt
import sys

def add_prior(var, levels, shape, eta, rho, b0, samp):
    lmb0 = ag.util.DisplacementFieldWavelet.make_lambdas(shape, levels, eta=eta, rho=rho)
    print samp.shape, var.shape, lmb0.shape
    return (b0 + samp*var/2) / (b0 * lmb0 + samp/2)
    

def surplus(costs, correct_label):
    """Metric for how close the correct score is from the current minimum score. Positive value is good."""
    mcost = costs[0][0]
    mdigit= costs[0][1]

    diff = 0 
    # The correct one is at the top
    if mdigit == correct_label:
        # Calculate where the next is
        for t in costs:
            cost, digit, mix_component = t
            if digit != correct_label:
                # We're dividing by cost, so that we don't promote classifiers that reduce the cost
                # for all classes, without improving the disciminative power.
                # mcost is the correct one
                diff = (cost - mcost)/mcost
                break

    # An incorrect one is at the top
    else:
        # Find the correct labels min cost
        for t in costs:
            cost, digit, mix_component = t 
            if digit == correct_label:
                # cost is the correct one
                diff = (mcost - cost)/cost
                break

    return diff 


# Classifer 
def classify(features, all_templates, means, variances, llh_variances=None, graylevels=None, graylevel_templates=None, samples=None, deformation='edges', correct_label=None, threshold_multiple=1.2, b0=None, eta=None, rho=None, debug_plot=False):
    # min loglikelihood
    min_cost = None
    min_which = None
    costs = []
    shape = features[-2:]
    for digit, templates in enumerate(all_templates):
        # Clip them, to avoid 0 probabilities

        for mix_component, template in enumerate(templates):
            #assert features.shape == template.shape
            # Compare mixture with features
            cost = -np.sum(features * np.log(template) + (1-features) * np.log(1-template))
            #print("Cost {0} digit {1} comp {2}".format(cost, digit, mix_component))
            costs.append( (cost, digit, mix_component) )
            if min_cost is None or cost < min_cost:
                min_cost = cost 
                min_which = (digit, mix_component) 

    info = {}

    if correct_label is not None:
        info['mixture_correct'] = correct_label == min_which[0]

    #info['contendors'] = len(checked)
    info['surplus_change'] = 0.0
    info['deformation'] = False
    info['num_contendors'] = 0 

    if deformation:
        costs = filter(lambda t: t[0] < min_cost * threshold_multiple, costs)
        costs.sort()

        # If all the costs left are the same digit, don't bother doing the deformation
        checked = [] 
        for t in costs:
            if t[1] not in checked:
                checked.append(t[1])
        

        if correct_label is not None:
            info['surplus_before'] = surplus(costs, correct_label)

        if len(checked) != 1:
            # Filter so that we have only one of each mixture (Not necessary, could even damage results!)
            if 0:
                for i, t in enumerate(costs):
                    if t[1] not in checked:
                        checked.append(t[1])
                    else:
                        del[costs[i]]

            # Do the deformation
            additional = {}
            new_costs = []
            info['deformation'] = True
            info['num_contendors'] = len(costs) 
            for t in costs:
                cost, digit, mix_component = t 
            
                var = variances[digit, mix_component]
                me = means[digit, mix_component]
                samp = samples[digit, mix_component] 
                if llh_variances is not None:
                    additional['llh_variances'] = llh_variances[digit, mix_component]
                penalty = None 
                levels = 3
    
                # Calculate the posterior variance
                # TODO: Maybe remove, this is usually done when arranging the coefficient file, and not here.
                if b0 and eta and rho and samples is not None:
                    raise Exception("Are you sure you want to do this?")
                    var = add_prior(var, shape, eta, rho, b0, samp)

                if deformation == 'edges':
                    F = all_templates[digit, mix_component]
                    I = features
                
                    imdef, information = ag.stats.bernoulli_deformation(F, I, wavelet='db4', penalty=penalty, means=me, variances=var, start_level=1, last_level=levels, debug_plot=debug_plot, tol=0.1, maxiter=200, **additional)

                elif deformation == 'intensity':
                    #assert originals is not None, "Intensity deformation requires originals"
                    F = graylevel_templates[digit, mix_component] 
                    I = graylevels

                    imdef, information = ag.stats.image_deformation(F, I, wavelet='db4', penalty=penalty, means=me, variances=var, start_level=1, last_level=levels, debug_plot=debug_plot, tol=0.00001, maxiter=50, **additional)

                # Kill if cancelled
                imdef or sys.exit(0)

                new_cost = information['cost']
                # Update the cost 
                ag.info("{0:4.1f} --> {1:4.1f}".format(cost, new_cost))

                new_costs.append( (new_cost, digit, mix_component) )

                if new_cost < min_cost:
                    min_cost = new_cost
                    min_which = (digit, mix_component)
                


            # Reevaluate the surplus!
            if correct_label is not None:
                info['surplus_after'] = surplus(new_costs, correct_label)
                info['surplus_change'] = info['surplus_after'] - info['surplus_before']

    if correct_label is not None:
        info['turned_correct'] = False
        info['turned_incorrect'] = False
        if (correct_label == min_which[0]) != info['mixture_correct']:
            if correct_label == min_which[0]:
                info['turned_correct'] = True
            else:
                info['turned_incorrect'] = True 
        
        info['mixture_correct'] = correct_label == min_which[0]

    info['comp'] = min_which[1]

    return min_which[0], info


