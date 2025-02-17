import numpy as np
from afra.tools.aux import umap, gvec, hvec
from afra.models.fg_models import fgmodel
from afra.models.bg_models import bgmodel
import emcee
from iminuit import Minuit
from dynesty import DynamicNestedSampler
from afra.tools.icy_decorator import icy


class fit(object):

    def __init__(self, data, fiducial, noise, covariance, background=None, foreground=None, solver='dynesty'):
        """
        Parameters
        ----------
        data : numpy.ndarray
            measurements' band-power matrix

        fiducial : numpy.ndarray
            CMB+noise fiducial band-power matrix

        noise : numpy.ndarray
            noise band-power matrix

        covariance : numpy.ndarray
            covariance matrix

        background : bgmodel object
            background model instance

        foreground : fgmodel object
            foreground model instance

        solver : str
            Fitting solver options, chosen from 'minuit', 'dynesty' and 'emcee'.
        """
        self.data = data
        self.fiducial = fiducial
        self.noise = noise
        self.covariance = covariance
        self.params = dict()  # initialized before back/fore-ground
        self.paramrange = dict()
        self.activelist = set()  # active Bayeisan parameters
        self.background = background
        self.foreground = foreground
        if (self._foreground is None and self._background is None):
            raise ValueError('no activated model')
        self.solver = solver
        self._rundict = {'minuit':self.run_minuit,'emcee':self.run_emcee,'dynesty':self.run_dynesty}

    @property
    def data(self):
        return self._data

    @property
    def fiducial(self):
        return self._fiducial

    @property
    def noise(self):
        return self._noise
        
    @property
    def covariance(self):
        return self._covariance

    @property
    def foreground(self):
        return self._foreground

    @property
    def background(self):
        return self._background

    @property
    def params(self):
        return self._params

    @property
    def paramrange(self):
        return self._paramrange

    @property
    def activelist(self):
        return self._activelist

    @property
    def solver(self):
        return self._solver

    @data.setter
    def data(self, data):
        assert isinstance(data, np.ndarray)
        assert (data.ndim == 4)
        if (np.isnan(data).any()):
            raise ValueError('encounter nan')
        self._data = data.copy()

    @fiducial.setter
    def fiducial(self, fiducial):
        assert isinstance(fiducial, np.ndarray)
        assert (fiducial.ndim == 4)
        if (np.isnan(fiducial).any()):
            raise ValueError('encounter nan')
        self._fiducial = fiducial.copy()

    @noise.setter
    def noise(self, noise):
        assert isinstance(noise, np.ndarray)
        assert (noise.ndim == 4)
        if (np.isnan(noise).any()):
            raise ValueError('encounter nan')
        self._noise = noise.copy()
        
    @covariance.setter
    def covariance(self, covariance):
        assert isinstance(covariance, np.ndarray)
        assert (covariance.ndim == 2)
        assert (covariance.shape[0] == covariance.shape[1])
        if (np.isnan(covariance).any()):
            raise ValueError('encounter nan')
        self._covariance = covariance.copy()

    @params.setter
    def params(self, params):
        assert isinstance(params, dict)
        self._params = params

    @paramrange.setter
    def paramrange(self, paramrange):
        assert isinstance(paramrange, dict)
        self._paramrange = paramrange

    @activelist.setter
    def activelist(self, activelist):
        assert isinstance(activelist, set)
        self._activelist = activelist

    @foreground.setter
    def foreground(self, foreground):
        if foreground is None:
            self._foreground = None
        else:
            assert isinstance(foreground, fgmodel)
            self._foreground = foreground
            # update from model
            self._params.update(self._foreground.params)
            self._paramrange.update(self._foreground.paramrange)

    @background.setter
    def background(self, background):
        if background is None:
            self._background = None
        else:
            assert isinstance(background, bgmodel)
            self._background = background
            # update from model
            self._params.update(self._background.params)
            self._paramrange.update(self._background.paramrange)

    @solver.setter
    def solver(self, solver):
        assert (solver in ('minuit','dynesty','emcee'))
        self._solver = solver

    def rerange(self, pdict):
        assert isinstance(pdict, dict)
        for name in pdict:
            if (name in self._paramrange.keys()):
                assert isinstance(pdict[name], (list,tuple))
                assert (len(pdict[name]) == 2)
                self._paramrange.update({name: pdict[name]})

    def run(self, kwargs):
        self._activelist = set(self._params.keys())
        if self._background is not None:
            self._activelist -= set(self._background.blacklist)
        if self._foreground is not None:
            self._activelist -= set(self._foreground.blacklist)
        return self._rundict[self._solver](kwargs)

    def run_minuit(self, kwargs={}):
        solver = Minuit(self._core_lsq, [0.5]*len(self._activelist), name=sorted(self._activelist))
        solver.limits = (0.,1.)
        solver.migrad()
        best = np.array(solver.values)
        solver.minos()
        err = np.array(solver.errors)
        names = sorted(self._activelist)
        for i in range(len(names)):
            low, high = self.paramrange[names[i]]
            best[i] = umap(best[i], [low, high])
            err[i] = umap(err[i], [low, high])
        return (best, err)

    def run_emcee(self, kwargs={'nwalker':100,'nstep':10000}):
        # emcee solver
        solver = emcee.EnsembleSampler(kwargs['nwalker'], len(self._activelist), self._core_likelihood)
        state = solver.run_mcmc(np.random.uniform(size=(kwargs['nwalker'],len(self._activelist))), kwargs['nstep']//10)  # burn-in
        solver.reset()
        state = solver.run_mcmc(state, kwargs['nstep'])
        tau = int(np.mean(solver.get_autocorr_time()))  # estimate integral auto-correlation time
        if (kwargs['nstep'] < 50*tau):  # if the pre-set step is not enough
            solver.reset()
            solver.run_mcmc(state, 100*tau)
        results = solver.get_chain(discard=10*tau, flat=True)
        names = sorted(self._activelist)
        for i in range(len(names)):
            low, high = self.paramrange[names[i]]
            results[:,i] = umap(results[:,i], [low, high])
        return results

    def run_dynesty(self, kwargs={}):
        # dynesty solver
        solver = DynamicNestedSampler(self._core_likelihood,self.prior,len(self._activelist))
        solver.run_nested(**kwargs)
        results = solver.results
        names = sorted(self._activelist)
        for i in range(len(names)):
            low, high = self.paramrange[names[i]]
            results.samples[:,i] = umap(results.samples[:,i], [low, high])
        return results

    def _core_likelihood(self, cube):
        if np.any(cube > 1.) or np.any(cube < 0.):
            return np.nan_to_num(-np.inf)
        name_list = sorted(self._activelist)
        for i in range(len(name_list)):
            name = name_list[i]
            tmp = umap(cube[i], self._paramrange[name])
            if self._foreground is not None:
                self._foreground.reset({name: tmp})
            if self._background is not None:
                self._background.reset({name: tmp})
        # predict data
        if self._foreground is None:
            return self.loglikeli(self._background.bandpower())
        elif self._background is None:
            return self.loglikeli(self._foreground.bandpower())
        else:
            return self.loglikeli(self._foreground.bandpower() + self._background.bandpower())

    def _core_lsq(self, cube):
        if np.any(cube > 1.) or np.any(cube < 0.):
            return np.nan_to_num(np.inf)
        name_list = sorted(self._activelist)
        for i in range(len(name_list)):
            name = name_list[i]
            tmp = umap(cube[i], self._paramrange[name])
            if self._foreground is not None:
                self._foreground.reset({name: tmp})
            if self._background is not None:
                self._background.reset({name: tmp})
        # predict data
        if self._foreground is None:
            return self.lsq(self._background.bandpower())
        elif self._background is None:
            return self.lsq(self._foreground.bandpower())
        else:
            return self.lsq(self._foreground.bandpower() + self._background.bandpower())

    def prior(self, cube):
        return cube  # flat prior


@icy
class gaussfit(fit):

    def __init__(self, data, fiducial, noise, covariance, background=None, foreground=None, solver='dynesty'):
        super(gaussfit, self).__init__(data,fiducial,noise,covariance,background,foreground,solver)

    def loglikeli(self, predicted):
        assert (predicted.shape == self._data.shape)
        diff = gvec(predicted+self._noise-self._data)
        if (np.isnan(diff).any()):
            raise ValueError('encounter nan')
        logl = -0.5*(np.vdot(diff,np.linalg.lstsq(self._covariance,diff,rcond=None)[0]))
        if np.isnan(logl):
            return np.nan_to_num(-np.inf)
        return logl

    def lsq(self, predicted):
        assert (predicted.shape == self._data.shape)
        diff = gvec(predicted+self._noise-self._data)
        if (np.isnan(diff).any()):
            raise ValueError('encounter nan')
        chi = np.vdot(diff,np.linalg.lstsq(self._covariance,diff,rcond=None)[0])
        if np.isnan(chi):
            return np.nan_to_num(np.inf)
        return chi


@icy
class hlfit(fit):

    def __init__(self, data, fiducial, noise, covariance, background=None, foreground=None, solver='dynesty', offset=None):
        super(hlfit, self).__init__(data,fiducial,noise,covariance,background,foreground,solver)
        self.offset = offset

    @property
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, offset):
        if offset is None:
            self._offset = np.zeros_like(self._noise,dtype=np.float32)
        else:
            assert (offset.shape == self._noise.shape)
            if (np.isnan(offset).any()):
                raise ValueError('encounter nan')
            self._offset = offset.copy()

    def loglikeli(self, predicted):
        assert (predicted.shape == self._data.shape)
        diff = hvec(predicted+self._noise+self._offset,self._data+self._offset,self._fiducial+self._noise+self._offset)
        if (np.isnan(diff).any()):
            raise ValueError('encounter nan')
        logl = -0.5*(np.vdot(diff,np.linalg.lstsq(self._covariance,diff,rcond=None)[0]))
        if np.isnan(logl):
            return np.nan_to_num(-np.inf)
        return logl

    def lsq(self, predicted):
        assert (predicted.shape == self._data.shape)
        diff = hvec(predicted+self._noise+self._offset,self._data+self._offset,self._fiducial+self._noise+self._offset)
        if (np.isnan(diff).any()):
            raise ValueError('encounter nan')
        chi = np.vdot(diff,np.linalg.lstsq(self._covariance,diff,rcond=None)[0])
        if np.isnan(chi):
            return np.nan_to_num(np.inf)
        return chi
