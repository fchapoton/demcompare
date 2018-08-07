#!/usr/bin/env python
# -*- coding: iso-8859-15 -*-

# Copyright (C) 2017-2018 Centre National d'Etudes Spatiales (CNES)

"""
Stats module of dsm_compare offers routines for stats computation and plot viewing

"""

import os
import copy
import numpy as np
from osgeo import gdal
from scipy import exp
from scipy.optimize import curve_fit
import math
import json
import collections
import csv
from dem_compare_lib.a3d_georaster import A3DGeoRaster


def gaus(x, a, x_zero, sigma):
    return a * exp(-(x - x_zero) ** 2 / (2 * sigma ** 2))


def roundUp(x, y):
    return int(math.ceil((x / float(y)))) * y


def getColor(nb_color=10):
    import matplotlib
    import matplotlib.pyplot as P
    if 10 < nb_color < 21:
        if matplotlib.__version__ >= '2.0.1':
            # According to matplotlib documentation the Vega colormaps are deprecated since the 2.0.1 and
            # disabled since 2.2.0
            x = P.cm.get_cmap('tab20')
        else:
            x = P.cm.get_cmap('Vega20')
    if nb_color < 11:
        if matplotlib.__version__ >= '2.0.1':
            x = P.cm.get_cmap('tab10')
        else:
            x = P.cm.get_cmap('Vega10')
    if nb_color > 20:
        raise NameError("Error : Too many colors requested")

    return np.array(x.colors)


def set_image_to_classify_from(cfg, coreg_dsm, coreg_ref):
    """
    Prepares images to classify dz errors from.
    If 'class_type' is slope, we use gdaldem. Otherwise we use user defined images but we need to rectify them.

    :param cfg: config file
    :param coreg_dsm : A3DDEMRaster, input coregistered DSM
    :param coreg_ref : A3DDEMRaster, input coregistered REF
    :return:
    """

    do_classification = False
    support_ref = None
    support_dsm = None
    if cfg['stats_opts']['class_type']:
        do_classification = True
        if cfg['stats_opts']['class_type'] == 'slope':
            # if class_type is 'slope' we must compute the slope(s) image(s)
            support_ref, support_dsm = create_slope_image(cfg, coreg_dsm, coreg_ref,
                                                          cfg['stats_opts']['cross_classification'])
        else:
            if cfg['stats_opts']['class_type'] == 'user':
                # if class_type is 'user' we must rectify the support image(s)
                support_ref, support_dsm = rectify_user_support_img(cfg, coreg_dsm,
                                                                    cfg['stats_opts']['cross_classification'])
                if not support_dsm :
                    # There can be no cross classification without a second support image to cross classify with
                    cfg['stats_opts']['cross_classification'] = False
            else:
                raise NameError('Only None, \'user\' and \'slope\' are supported options for '
                                '[\'stats_opts\'][\'class_type\']')

    return do_classification, cfg['stats_opts']['cross_classification'], support_ref, support_dsm


def create_slope_image(cfg, coreg_dsm, coreg_ref, do_cross_classification=False):
    """
    Computes the slope image of coreg_ref and, optionally,of coreg_dsm

    One shall notice that a scale factor between plani and alti resolutions is computed via the mean of both
    resolution dimensions. This assumes pixels are roughly squares.

    :param cfg: la configuration complete du lancement de dsm_compare
    :param coreg_dsm : A3DDEMRaster, input coregistered DSM
    :param coreg_ref : A3DDEMRaster, input coregistered REF
    :param do_cross_classification: activation de la cross classification
    :return: A3DGeoRaster slope ref and slope dsm
    """

    # PROCESS THE REFERENCE SLOPED IMAGE
    cfg['stats_results']['images']['list'].append('Ref_support')
    cfg['stats_results']['images']['Ref_support'] = copy.deepcopy(cfg['alti_results']['rectifiedRef'])
    if 'georef' in cfg['stats_results']['images']['Ref_support']:
        cfg['stats_results']['images']['Ref_support'].pop('georef')
    cfg['stats_results']['images']['Ref_support'].pop('nb_points')
    cfg['stats_results']['images']['Ref_support'].pop('nb_valid_points')
    cfg['stats_results']['images']['Ref_support']['path'] = os.path.join(cfg['outputDir'],
                                                                         'Ref_support.tif')

    # Compute slope
    slope_ref, aspect_ref = coreg_ref.get_slope_and_aspect(degree=False)
    slope_ref_georaster = A3DGeoRaster.from_raster(slope_ref,
                                                   coreg_ref.trans,
                                                   "{}".format(coreg_ref.srs.ExportToProj4()),
                                                   nodata=-32768)
    slope_ref_georaster.save_geotiff(cfg['stats_results']['images']['Ref_support']['path'])
    cfg['stats_results']['images']['Ref_support']['nodata'] = slope_ref_georaster.nodata

    if do_cross_classification:
        # PROCESS THE SECONDARY (DSM) SLOPED IMAGE
        cfg['stats_results']['images']['list'].append('DSM_support')
        cfg['stats_results']['images']['DSM_support'] = copy.deepcopy(cfg['alti_results']['rectifiedDSM'])
        if 'georef' in cfg['stats_results']['images']['DSM_support']:
            cfg['stats_results']['images']['DSM_support'].pop('georef')
        cfg['stats_results']['images']['DSM_support'].pop('nb_points')
        cfg['stats_results']['images']['DSM_support'].pop('nb_valid_points')
        cfg['stats_results']['images']['DSM_support']['path'] = os.path.join(cfg['outputDir'],
                                                                                     'DSM_support.tif')

        # Compute slope
        slope_dsm, aspect_dsm = coreg_dsm.get_slope_and_aspect(degree=False)
        slope_dsm_georaster = A3DGeoRaster.from_raster(slope_dsm,
                                                       coreg_dsm.trans,
                                                       "{}".format(coreg_dsm.srs.ExportToProj4()),
                                                       nodata=-32768)
        slope_dsm_georaster.save_geotiff(cfg['stats_results']['images']['DSM_support']['path'])
        cfg['stats_results']['images']['DSM_support']['nodata'] = slope_dsm_georaster.nodata

        return slope_ref_georaster, slope_dsm_georaster
    return slope_ref_georaster, None


def rectify_user_support_img(cfg, coreg_dsm, do_cross_classification = False):
    """
    Rectify image(s) set by the user to serve as classification support.
    It is assumed that this images metadata contain the nan value if there is one.

    :param cfg:
    :param coreg_dsm : A3DDEMRaster, input coregistered DSM
    :param do_cross_classification:
    :return:
    """

    #
    # Reproject user support image on top of coreg dsm and coreg ref (which are coregistered together)
    #
    rectified_support_ref = None
    rectified_support_dsm = None
    if 'class_support_ref' in cfg['stats_opts']:
        input_support_ref = A3DGeoRaster(str(cfg['stats_opts']['class_support_ref']), nodata=-32768)
        rectified_support_ref = input_support_ref.reproject(coreg_dsm.srs, int(coreg_dsm.nx), int(coreg_dsm.ny),
                                                            coreg_dsm.footprint[0], coreg_dsm.footprint[3],
                                                            coreg_dsm.xres, coreg_dsm.yres, nodata=input_support_ref.nodata)
        rectified_support_ref.save_geotiff(os.path.join(cfg['outputDir'], 'Ref_support.tif'))
    if 'class_support_dsm' in cfg['stats_opts'] and do_cross_classification is True:
        input_support_dsm = A3DGeoRaster(str(cfg['stats_opts']['class_support_dsm']), nodata=-32768)
        # Keep in mind that the DSM geo ref has been shifted, hence we need to shift the support here
        x_off = cfg['plani_results']['dx']['bias_value'] / input_support_dsm.xres
        y_off = cfg['plani_results']['dy']['bias_value'] / input_support_dsm.yres
        input_support_dsm = input_support_dsm.geo_translate(x_off - 0.5, -y_off - 0.5, system='pixel')
        rectified_support_dsm = input_support_dsm.reproject(coreg_dsm.srs, int(coreg_dsm.nx), int(coreg_dsm.ny),
                                                            coreg_dsm.footprint[0], coreg_dsm.footprint[3],
                                                            coreg_dsm.xres, coreg_dsm.yres, nodata=input_support_dsm.nodata,
                                                            interp_type=gdal.GRA_NearestNeighbour)
        rectified_support_dsm.save_geotiff(os.path.join(cfg['outputDir'], 'DSM_support.tif'))

    #
    # Save results into cfg
    #
    if rectified_support_ref:
        cfg['stats_results']['images']['Ref_support'] = {'path': rectified_support_ref.ds_file}
        cfg['stats_results']['images']['Ref_support']['nodata'] = rectified_support_ref.nodata
    if rectified_support_dsm:
        cfg['stats_results']['images']['DSM_support'] = {'path': rectified_support_dsm.ds_file}
        cfg['stats_results']['images']['DSM_support']['nodata'] = rectified_support_dsm.nodata

    return rectified_support_ref, rectified_support_dsm


def get_sets_labels_and_names(class_type, class_rad_range):
    """
    Get sets' labels and sets' names

    :param class_type: 'slope' or 'user'
    :param class_rad_range: list defining class ranges such as [0 10 25 100]
    :return sets labels and names
    """

    sets_label_list = []
    sets_name_list = []

    for i in range(0, len(class_rad_range)):
        if i == len(class_rad_range) - 1:
            if class_type == 'slope':
                sets_label_list.append(r'$\nabla$ > {}%'.format(class_rad_range[i]))
            else:
                sets_label_list.append('val > {}%'.format(class_rad_range[i]))
            sets_name_list.append('[{}; inf['.format(class_rad_range[i]))
        else:
            if class_type == 'slope':
                sets_label_list.append(r'$\nabla \in$ [{}% ; {}%]'.format(class_rad_range[i], class_rad_range[i + 1]))
            else:
                sets_label_list.append(r'val $\in$ [{}% ; {}%]'.format(class_rad_range[i], class_rad_range[i + 1]))
            sets_name_list.append('[{}; {}]'.format(class_rad_range[i], class_rad_range[i + 1]))

    return sets_label_list, sets_name_list


def create_sets(img_to_classify,
                             sets_rad_range,
                             tmpDir='.',
                             output_descriptor=None):
    """
    Returns a list of boolean arrays. Each array defines a set. The sets partition / classify the image.
    A boolean array defines indices to kept for the associated set / class.

    :param img_to_classify: A3DGeoRaster
    :param sets_rad_range: list of values that defines the radiometric ranges of each set
    :param tmpDir: temporary directory to which store temporary data
    :param output_descriptor: dictionary with 'path' and 'nodata' keys for the output classified img (png format)
    :return: list of boolean arrays
    """

    # create output dataset if required
    if output_descriptor:
        driver_mem = gdal.GetDriverByName("MEM")
        output_tmp_name = os.path.join(tmpDir, 'tmp.mem')
        output_dataset = driver_mem.Create(output_tmp_name,
                                           img_to_classify.nx,
                                           img_to_classify.ny,
                                           4, gdal.GDT_Byte)
        output_v = np.ones((4, img_to_classify.ny, img_to_classify.nx), dtype=np.int8) * 255

    # use radiometric ranges to classify
    sets_colors = np.multiply(getColor(len(sets_rad_range)), 255)
    output_sets_def = []
    for idx in range(0, len(sets_rad_range)):
        if idx == len(sets_rad_range) - 1:
            output_sets_def.append(np.apply_along_axis(lambda x:(sets_rad_range[idx] <= x),
                                                       0, img_to_classify.r))
            if output_descriptor:
                for i in range(0, 3):
                    output_v[i][sets_rad_range[idx] <= img_to_classify.r] = sets_colors[idx][i]
        else:
            output_sets_def.append(np.apply_along_axis(lambda x:(sets_rad_range[idx] <= x)*(x < sets_rad_range[idx+1]),
                                                       0, img_to_classify.r))
            if output_descriptor:
                for i in range(0,3):
                    output_v[i][(sets_rad_range[idx] <= img_to_classify.r) *
                                (img_to_classify.r < sets_rad_range[idx + 1])] = sets_colors[idx][i]

    # deals with the nan value (we choose black color for nan value since it is not part of the colormap chosen)
    if output_descriptor:
        for i in range(0,4):
            output_v[i][(np.isnan(img_to_classify.r)) + (img_to_classify.r == img_to_classify.nodata)] = \
                output_descriptor['nodata'][i]
    for idx in range(0, len(sets_rad_range)):
        output_sets_def[idx][(np.isnan(img_to_classify.r)) + (img_to_classify.r == img_to_classify.nodata)] = False

    # write down the result then translate from MEM to PNG
    if output_descriptor:
        [output_dataset.GetRasterBand(i + 1).WriteArray(output_v[i]) for i in range(0, 4)]
        gdal.GetDriverByName('PNG').CreateCopy(output_descriptor['path'], output_dataset)
        output_dataset = None

    return output_sets_def, sets_colors / 255


def cross_class_apha_bands(ref_png_desc, dsm_png_desc, ref_sets, dsm_sets, tmpDir='.'):
    """
    Set accordingly the alpha bands of both png : for pixels where classification differs, alpha band is transparent

    :param ref_png_desc: dictionary with 'path' and 'nodata' keys for the ref support classified img (png format)
    :param dsm_png_desc: dictionary with 'path' and 'nodata' keys for the ref support classified img (png format)
    :param ref_sets: list of ref sets (ref_png class)
    :param dsm_sets: list of dsm sets (dsm_png class)
    :param tmpDir: where to store temporary data
    :return:
    """

    ref_dataset = gdal.Open(ref_png_desc['path'])
    dsm_dataset = gdal.Open(dsm_png_desc['path'])
    ref_mem_dataset = gdal.GetDriverByName('MEM').CreateCopy(os.path.join(tmpDir, 'tmp_ref.mem'), ref_dataset)
    dsm_mem_dataset = gdal.GetDriverByName('MEM').CreateCopy(os.path.join(tmpDir, 'tmp_sec.mem'), dsm_dataset)
    ref_aplha_v = ref_dataset.GetRasterBand(4).ReadAsArray()
    dsm_aplha_v = dsm_dataset.GetRasterBand(4).ReadAsArray()
    ref_dataset = None
    dsm_dataset = None

    # Combine pairs of sets together (meaning first ref set with first dsm set)
    # -> then for each single class / set, we know which pixels are coherent between both ref and dsm support img
    # -> combine_sets[0].shape[0] = number of sets (classes)
    # -> combine_sets[0].shape[1] = number of pixels inside a single DSM
    combine_sets = np.array([ref_sets[i][:]==dsm_sets[i][:] for i in range(0,len(ref_sets))])

    # Merge all combined sets together so that if a pixel's value across all sets is not always True then the alpha
    # band associated value is transparent (=0) since this pixel is not classified the same way between both support img
    # -> np.all gives True when the pixel has been coherently classified since its bool val were sets pairwise identical
    # -> np.where(...) gives indices of pixels for which cross classification is incoherent (np.all(...)==False)
    # -> those pixels as set transparent (=0) in chanel 4
    incoherent_indices=np.where(np.all(combine_sets,axis=0)==False)
    ref_aplha_v[incoherent_indices] = 0
    dsm_aplha_v[incoherent_indices] = 0

    # Write down the results
    ref_mem_dataset.GetRasterBand(4).WriteArray(ref_aplha_v)
    dsm_mem_dataset.GetRasterBand(4).WriteArray(dsm_aplha_v)

    # From MEM to PNG (GDAL does not seem to handle well PNG format)
    gdal.GetDriverByName('PNG').CreateCopy(ref_png_desc['path'], ref_mem_dataset)
    gdal.GetDriverByName('PNG').CreateCopy(dsm_png_desc['path'], dsm_mem_dataset)


def create_masks(alti_map,
                 do_classification=False, ref_support=None,
                 do_cross_classification=False, ref_support_classified_desc=None,
                 remove_outliers = True):
    """
    Compute Masks for every required modes :
    -> the 'standard' mode where the mask stands for nan values inside the error image with the nan values
       inside the ref_support_desc when do_classification is on & it also stands for outliers free values
    -> the 'coherent-classification' mode which is the 'standard' mode where only the pixels for which both sets (dsm
       and reference) are coherent
    -> the 'incoherent-classification' mode which is 'coherent-classification' complementary

    :param alti_map: A3DGeoRaster, alti differences
    :param do_classification: boolean indicated wether or not the classification is activated
    :param ref_support: A3DGeoRaster
    :param do_cross_classification: boolean indicated wether or not the cross classification is activated
    :param ref_support_classified_desc: dict with 'path' and 'nodata' keys for the ref support image classified
    :param remove_outliers: boolean, set to True (default) to return a no_outliers mask
    :return: list of masks, associated modes, and error_img read as array
    """

    def get_ouliersfree_mask(array, no_nan_mask):
        array_without_nan = array[np.where(no_nan_mask==True)]
        mu = np.mean(array_without_nan)
        sigma = np.std(array_without_nan)
        return np.apply_along_axis(lambda x: (x > mu - 3 * sigma) * (x < mu + 3 * sigma), 0, array)

    def get_nonan_mask(array, nan_value):
        return np.apply_along_axis(lambda x: (~np.isnan(x))*(x != nan_value), 0, array)

    modes = []
    masks = []

    # Starting with the 'standard' mask with no nan values
    modes.append('standard')
    masks.append(get_nonan_mask(alti_map.r, alti_map.nodata))

    # Create no outliers mask if required
    no_outliers = None
    if remove_outliers:
        no_outliers = get_ouliersfree_mask(alti_map.r, masks[0])

    # If the classification is on then we also consider ref_support nan values
    if do_classification:
        masks[0] *= get_nonan_mask(ref_support.r, ref_support.nodata)

    # Carrying on with potentially the cross classification masks
    if do_classification and do_cross_classification:
        modes.append('coherent-classification')
        ref_support_classified_dataset = gdal.Open(ref_support_classified_desc['path'])
        ref_support_classified_val = ref_support_classified_dataset.GetRasterBand(4).ReadAsArray()
        # so we get rid of what are actually 'nodata' and incoherent values as well
        coherent_mask = get_nonan_mask(ref_support_classified_val, ref_support_classified_desc['nodata'][0])
        masks.append(masks[0] * coherent_mask)

        # Then the incoherent one
        modes.append('incoherent-classification')
        masks.append(masks[0] * ~coherent_mask)

    return masks, modes, no_outliers


def stats_computation(array):
    """
    Compute stats for a specific array

    :param array: numpy array
    :return: dict with stats name and values
    """
    if array.size:
        res = {
            'nbpts': array.size,
            'max':float(np.max(array)),
            'min': float(np.min(array)),
            'mean': float(np.mean(array)),
            'std': float(np.std(array)),
            'rmse': float(np.sqrt(np.mean(array*array))),
            'median': float(np.nanmedian(array)),
            'nmad': float(1.4826 * np.nanmedian(np.abs(array-np.nanmedian(array)))),
            'sum_err': float(np.sum(array)),
            'sum_err.err': float(np.sum(array * array)),
        }
    else:
        res = {
            'nbpts': array.size,
            'max': np.nan,
            'min': np.nan,
            'mean': np.nan,
            'std': np.nan,
            'rmse': np.nan,
            'median': np.nan,
            'nmad': np.nan,
            'sum_err': np.nan,
            'sum_err.err': np.nan,
        }
    return res


def get_stats(dz_values, to_keep_mask=None, no_outliers_mask=None, sets=None, sets_labels=None, sets_names=None):
    """
    Get Stats for a specific array, considering potentially subsets of it

    :param dz_values: errors
    :param to_keep_mask: boolean mask with True values for pixels to use
    :param sets: list of sets (boolean arrays that indicate which class a pixel belongs to)
    :param sets_labels: label associated to the sets
    :param sets_names: name associated to the sets
    :return: list of dictionary (set_name, nbpts, %(out_of_all_pts), max, min, mean, std, rmse, ...)
    """

    def nighty_percentile(array):
        """
        Compute the maximal error for the 90% smaller errors

        :param array:
        :return:
        """
        return np.nanpercentile(np.abs(array - np.nanmean(array)), 90)

    # Init
    output_list = []
    nb_total_points = dz_values.size
    # - if a mask is not set, we set it with True values only so that it has no effect
    if to_keep_mask is None:
        to_keep_mask = np.ones(dz_values.shape)
    if no_outliers_mask is None:
        no_outliers_mask = np.ones(dz_values.shape)

    # Computing first set of values with all pixels considered -except the ones masked or the outliers-
    output_list.append(stats_computation(dz_values[np.where((to_keep_mask*no_outliers_mask) == True)]))
    # - we add standard information for later use
    output_list[0]['set_label'] = 'all'
    output_list[0]['set_name'] = 'All classes considered'
    output_list[0]['%'] = 100 * float(output_list[0]['nbpts']) / float(nb_total_points)
    # - we add computation of nighty percentile (of course we keep outliers for that so we use dz_values as input array)
    output_list[0]['90p'] = nighty_percentile(dz_values[np.where(to_keep_mask==True)])

    # Computing stats for all sets (sets are a partition of all values)
    if sets is not None and sets_labels is not None and sets_names is not None:
        for set_idx in range(0,len(sets)):
            set = sets[set_idx] * to_keep_mask * no_outliers_mask

            data = dz_values[np.where(set == True)]
            output_list.append(stats_computation(data))
            output_list[set_idx+1]['set_label'] = sets_labels[set_idx]
            output_list[set_idx+1]['set_name'] = sets_names[set_idx]
            output_list[set_idx+1]['%'] = 100 * float(output_list[set_idx+1]['nbpts']) / float(nb_total_points)
            output_list[set_idx+1]['90p'] = nighty_percentile(dz_values[np.where((sets[set_idx] * to_keep_mask) == True)])

    return output_list


def dem_diff_plot(dem_diff, title='', plot_file='dem_diff.png', display=False):
    """
    Simple img show after outliers removal

    :param dem_diff: A3DGeoRaster,
    :param title: string, plot title
    :param plot_file: path and name for the saved plot (used when display if False)
    :param display: boolean, set to True if display is on, otherwise the plot is saved to plot_file location
    :return:
    """

    #
    # Plot initialization
    #
    # -> import what is necessary for plot purpose
    import matplotlib as mpl
    mpl.rc('font', size=6)
    import matplotlib.pyplot as P

    #
    # Plot
    #
    P.figure(1, figsize=(7.0, 8.0))
    P.title(title)
    nmad = float(1.4826 * np.nanmedian(np.abs(dem_diff.r-np.nanmedian(dem_diff.r))))
    maxval = 3 * nmad
    P.imshow(dem_diff.r, vmin=-maxval, vmax=maxval)
    cb = P.colorbar()
    cb.set_label('Elevation differences (m)')

    #
    # Show or Save
    #
    if display is False:
        P.savefig(plot_file, dpi=100, bbox_inches='tight')
    else:
        P.show()
    P.close()


def plot_histograms(input_array, bin_step=0.1, to_keep_mask=None,
                       sets=None, sets_labels=None, sets_colors=None,
                       plot_title='', outdir='.', save_prefix='', display=False):
    """
    Creates a histogram plot for all sets given and saves them on disk.
    Note : If more than one set is given, than all the remaining sets are supposed to partitioned the first one. Hence
           in the contribution plot the first set is not considered and all percentage are computed in regards of the
           number of points within the first set (which is supposed to contain them all)

    :param input_array: data to plot
    :param bin_step: histogram bin step
    :param to_keep_mask: boolean mask with True values for pixels to use
    :param sets: list of sets (boolean arrays that indicate which class a pixel belongs to)
    :param set_labels: name associated to the sets
    :param sets_colors: color set for plotting
    :param sets_stats: where should be retrived mean and std values for all sets
    :param plot_title: plot primary title
    :param outdir: directory where histograms are to be saved
    :param save_prefix: prefix to the histogram files saved by this method
    :return: list saved files
    """

    saved_files=[]
    saved_labels=[]
    saved_colors=[]

    #
    # Plot initialization
    #
    # -> import what is necessary for plot purpose
    import matplotlib as mpl
    mpl.rc('font', size=6)
    import matplotlib.pyplot as P
    from matplotlib import gridspec

    # -> bins should rely on [-A;A], A being the higher absolute error value (all histograms rely on the same bins range)
    if to_keep_mask is not None:
        borne = np.max([abs(np.nanmin(input_array[np.where(to_keep_mask==True)])),
                        abs(np.nanmax(input_array[np.where(to_keep_mask==True)]))])
    else:
        borne = np.max([abs(np.nanmin(input_array)), abs(np.nanmax(input_array))])
    bins = np.arange(-roundUp(borne, bin_step), roundUp(borne, bin_step)+bin_step, bin_step)
    np.savetxt(os.path.join(outdir, save_prefix+'bins'+'.txt'), [bins[0],bins[len(bins)-1], bin_step])

    # -> set figures shape, titles and axes
    #    -> first figure is just one plot of normalized histograms
    P.figure(1, figsize=(7.0, 8.0))
    P.title('Normalized histograms')
    P.xlabel('Errors (meter)')
    #    -> second one is two plots : fitted by gaussian histograms & classes contributions
    P.figure(2, figsize=(7.0, 8.0))
    gs = gridspec.GridSpec(1,2, width_ratios=[10,1])
    P.subplot(gs[0])
    P.suptitle(plot_title)
    P.title('Errors fitted by a gaussian')
    P.xlabel('Errors (in meter)')
    P.subplot(gs[1])
    P.title('Classes contributions')
    P.xticks(np.arange(1), '')

    #
    # Plot creation
    #
    cumulative_percent = 0
    set_zero_size = 0
    if sets is not None and sets_labels is not None and sets_colors is not None:
        for set_idx in range(0,len(sets)):
            # -> restricts to input data
            if to_keep_mask is not None:
                sets[set_idx] = sets[set_idx] * to_keep_mask
            data = input_array[np.where(sets[set_idx] == True)]

            # -> empty data is not plotted
            if data.size:
                mean = np.mean(data)
                std = np.std(data)
                nb_points_as_percent = 100 * float(data.size) / float(input_array.size)
                if set_idx != 0:
                    set_contribution = 100 * float(data.size) / float(set_zero_size)
                    cumulative_percent += set_contribution
                else:
                    set_zero_size = data.size

                try:
                    P.figure(1)
                    n, bins, patches = P.hist(data, bins, normed=True,
                                              label=sets_labels[set_idx], color=sets_colors[set_idx])
                    popt, pcov = curve_fit(gaus, bins[0:bins.shape[0] - 1] + int(bin_step / 2), n,
                                           p0=[1, mean, std])
                    P.figure(2)
                    P.subplot(gs[0])
                    l = P.plot(np.arange(bins[0], bins[bins.shape[0] - 1], bin_step / 10),
                               gaus(np.arange(bins[0], bins[bins.shape[0] - 1], bin_step / 10), *popt),
                               color=sets_colors[set_idx], linewidth=1,
                               label=' '.join([sets_labels[set_idx],
                                               r'$\mu$ {0:.2f}m'.format(mean),
                                               r'$\sigma$ {0:.2f}m'.format(std),
                                               '{0:.2f}% points'.format(nb_points_as_percent)]))
                    if set_idx != 0:
                        P.subplot(gs[1])
                        P.bar(1, set_contribution, 0.05, color=sets_colors[set_idx],
                              bottom=cumulative_percent - set_contribution,
                              label='test')  # 1 is the x location and 0.05 is the width (label is not printed)
                        P.text(1, cumulative_percent - 0.5 * set_contribution, '{0:.2f}'.format(set_contribution),
                               weight='bold', horizontalalignment='left')
                except RuntimeError:
                    print('No fitted gaussian plot created as curve_fit failed to converge')
                    raise

                # save outputs (plot files and name of labels kept)
                saved_labels.append(sets_labels[set_idx])
                saved_colors.append(sets_colors[set_idx])
                saved_file = os.path.join(outdir, save_prefix + str(set_idx) + '.npy')
                saved_files.append(saved_file)
                np.save(saved_file, n)

    #
    # Plot save or show
    #
    P.figure(1)
    P.legend()
    if display is False:
        P.savefig(os.path.join(outdir,'AltiErrors-Histograms_'+save_prefix+'.png'),
                  dpi=100, bbox_inches='tight')
    P.figure(2)
    P.subplot(gs[0])
    P.legend(loc="upper left")
    if display is False:
        P.savefig(os.path.join(outdir,'AltiErrors-Histograms_FittedWithGaussians_'+save_prefix+'.png'),
                  dpi=100, bbox_inches='tight')
    else:
        P.show()

    P.figure(1)
    P.close()
    P.figure(2)
    P.close()

    return saved_files, saved_colors, saved_labels


def save_results(output_json_file, stats_list, labels_plotted=None, plot_files=None, plot_colors=None, to_csv=False):
    """
    Saves stats into specific json file (and optionally to csv file)

    :param output_json_file: file in which to save
    :param stats_list: all the stats to save (one element per label)
    :param labels_plotted: list of labels plotted
    :param plot_files: list of plot files associdated to the labels_plotted
    :param plot_colors: list of plot colors associdated to the labels_plotted
    :param to_csv: boolean, set to True to save to csv format as well (default False)
    :return:
    """

    results = {}
    for stats_index, stats_elem  in enumerate(stats_list):
        results[str(stats_index)] = stats_elem
        if labels_plotted is not None and plot_files is not None and plot_colors is not None :
            if stats_elem['set_label'] in labels_plotted:
                try:
                    results[str(stats_index)]['plot_file'] = plot_files[labels_plotted.index(stats_elem['set_label'])]
                    results[str(stats_index)]['plot_color'] = tuple(plot_colors[labels_plotted.index(stats_elem['set_label'])])
                except:
                    print('Error: plot_files and plot_colors should have same dimension as labels_plotted')
                    raise

    with open(output_json_file, 'w') as outfile:
        json.dump(results, outfile, indent=4)

    if to_csv:
        # Print the merged results into a csv file with only "important" fields and extended fieldnames
        # - create filename
        csv_filename = os.path.join(os.path.splitext(output_json_file)[0]+'.csv')
        # - fill csv_results with solely the filed required
        csv_results = collections.OrderedDict()
        for set_idx in range(0, len(results)):
            key = str(set_idx)
            csv_results[key] = collections.OrderedDict()
            csv_results[key]['Set Name'] = results[key]['set_name']
            csv_results[key]['% Of Valid Points'] = results[key]['%']
            csv_results[key]['Max Error'] = results[key]['max']
            csv_results[key]['Min Error'] = results[key]['min']
            csv_results[key]['Mean Error'] = results[key]['mean']
            csv_results[key]['Error std'] = results[key]['std']
            csv_results[key]['RMSE'] = results[key]['rmse']
            csv_results[key]['Median Error'] = results[key]['median']
            csv_results[key]['NMAD'] = results[key]['nmad']
            csv_results[key]['90 percentile'] = results[key]['90p']
        # - writes the results down as csv format
        with open(csv_filename, 'w') as csvfile:
            fieldnames = csv_results["0"].keys()
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_NONNUMERIC)

            writer.writeheader()
            for set in csv_results:
                writer.writerow(csv_results[set])


def alti_diff_stats(cfg, dsm, ref, alti_map, display=False):
    """
    Computes alti error stats with graphics and tables support.

    If cfg['stats_opt']['class_type'] is not None those stats can be partitioned into different sets. The sets
    are radiometric ranges used to classify a support image. May the support image be the slope image associated
    with the reference DSM then the sets are slopes ranges and the stats are provided by classes of slopes ranges.

    Actually, if cfg['stats_opt']['class_type'] is 'slope' then computeStats first computes slope image and classify
    stats over slopes. If cfg['stats_opt']['class_type'] is 'user' then a user support image must be given to be
    classified over cfg['stats_opt']['class_rad_range'] intervals so it can partitioned the stats.

    When cfg['stats_opt']['class_type']['class_coherent'] is set to True then two images to classify are required
    (one associated with the reference DEM and one with the other one). The results will be presented through 3 modes:
    -standard mode,
    -coherent mode where only alti errors values associated with coherent classes between both classified images are used
    -and, incoherent mode (the coherent complementary one).

    :param cfg: config file
    :param dsm: A3GDEMRaster, dsm
    :param ref: A3GDEMRaster, coregistered ref
    :param alti_map: A3DGeoRaster, dsm - ref
    :param display: boolean, display option (set to False to save plot on file system)
    :return:
    """

    #
    # If we are to classify the 'z' stats then we make sure we have what it takes
    #
    do_classify_results, do_cross_classification, support_ref, support_dsm = set_image_to_classify_from(cfg, dsm, ref)

    #
    # Get back label list from sets ranges of values
    #
    sets_names = None
    sets_labels = None
    if do_classify_results:
        sets_labels, sets_names = get_sets_labels_and_names(cfg['stats_opts']['class_type'],
                                                            cfg['stats_opts']['class_rad_range'])

    #
    # If required, create sets definitions (boolean arrays where True means the associated index is part of the set)
    #
    ref_classified_img_descriptor = None
    dsm_classified_img_descriptor = None
    ref_sets_def = dsm_sets_def = None
    if do_classify_results:
        cfg['stats_results']['images']['list'].append('Ref_support_classified')
        cfg['stats_results']['images']['Ref_support_classified'] = {}
        ref_classified_img_descriptor = cfg['stats_results']['images']['Ref_support_classified']
        ref_classified_img_descriptor['path'] = os.path.join(cfg['outputDir'], 'Ref_support_classified.png')
        ref_classified_img_descriptor['nodata'] = [0, 0, 0, 0]
        ref_sets_def, sets_color = create_sets(support_ref, cfg['stats_opts']['class_rad_range'],
                                               tmpDir=cfg['tmpDir'], output_descriptor=ref_classified_img_descriptor)

        if do_cross_classification:
            cfg['stats_results']['images']['list'].append('DSM_support_classified')
            cfg['stats_results']['images']['DSM_support_classified'] = {}
            dsm_classified_img_descriptor = cfg['stats_results']['images']['DSM_support_classified']
            dsm_classified_img_descriptor['path'] = os.path.join(cfg['outputDir'], 'DSM_support_classified.png')
            dsm_classified_img_descriptor['nodata'] = [0, 0, 0, 0]
            dsm_sets_def, sets_color = create_sets(support_dsm, cfg['stats_opts']['class_rad_range'],
                                                   tmpDir=cfg['tmpDir'], output_descriptor=dsm_classified_img_descriptor)

    #
    # If cross-classification is 'on' we set the alphas bands transparent where ref and dsm support classified differ
    #
    if do_classify_results and do_cross_classification:
        cross_class_apha_bands(ref_classified_img_descriptor, dsm_classified_img_descriptor, ref_sets_def, dsm_sets_def)

    #
    # Get the masks to apply to dz array for all stats configurations (we call it 'mode')
    #
    to_keep_masks, modes, no_outliers_mask = create_masks(alti_map, do_classify_results, support_ref,
                                                          do_cross_classification, ref_classified_img_descriptor,
                                                          remove_outliers = True)

    #
    # Next is done for all modes
    #
    cfg['stats_results']['modes'] = {}
    for mode in range(0, len(modes)):
        #
        # Compute stats for all sets of a single mode
        #
        mode_stats = get_stats(alti_map.r,
                               to_keep_mask=to_keep_masks[mode],
                               no_outliers_mask=no_outliers_mask,
                               sets=ref_sets_def,
                               sets_labels=sets_labels,
                               sets_names=sets_names)

        # TODO (peut etre prevoir une activation optionnelle du plotage...)
        #
        # Create plots for the actual mode and for all sets
        #
        # -> we set the title here and we chose to print the bias and % nan values as part of this title:
        dx = cfg['plani_results']['dx']
        dy = cfg['plani_results']['dy']
        biases = {'dx': {'value_m': dx['bias_value'], 'value_p': dx['bias_value'] / ref.xres},
                  'dy': {'value_m': dy['bias_value'], 'value_p': dy['bias_value'] / ref.yres}}
        rect_ref_cfg = cfg['alti_results']['rectifiedRef']
        rect_dsm_cfg = cfg['alti_results']['rectifiedDSM']
        title = ['MNT quality performance']
        title.append('(mean biases : '
                     'dx : {:.2f}m (roughly {:.2f}pixel); '
                     'dy : {:.2f}m (roughly {:.2f}pixel);'.format(biases['dx']['value_m'],
                                                                  biases['dx']['value_p'],
                                                                  biases['dy']['value_m'],
                                                                  biases['dy']['value_p']))
        title.append('(holes or no data stats: '
                     'Reference DSM  % nan values : {:.2f}%; '
                     'DSM to compare % nan values : {:.2f}%;'.format(100 * (1 - float(rect_ref_cfg['nb_valid_points'])
                                                                            / float(rect_ref_cfg['nb_points'])),
                                                                     100 * (1 - float(rect_dsm_cfg['nb_valid_points'])
                                                                            / float(rect_dsm_cfg['nb_points']))))
        # -> we are then ready to do some plots !
        if mode is not None:
            plot_files, plot_colors, labels_saved = plot_histograms(alti_map.r,
                                                                    bin_step=cfg['stats_opts']['alti_error_threshold']['value'],
                                                                    to_keep_mask=(to_keep_masks[mode] * no_outliers_mask),
                                                                    sets=[np.ones((alti_map.r.shape),dtype=bool)]+ref_sets_def,
                                                                    sets_labels=['all']+sets_labels,
                                                                    sets_colors=np.array([(0,0,0)]+list(sets_color)),
                                                                    plot_title='\n'.join(title),
                                                                    outdir=cfg['outputDir'],
                                                                    save_prefix=modes[mode],
                                                                    display=display)
        else:
            plot_files = []
            plot_colors = []
            labels_saved = []

        #
        # Save results as .json and .csv file
        #
        cfg['stats_results']['modes'][modes[mode]] = os.path.join(cfg['outputDir'],'stats_results_'+modes[mode]+'.json')
        save_results(cfg['stats_results']['modes'][modes[mode]],
                     mode_stats,
                     labels_plotted=labels_saved,
                     plot_files=plot_files,
                     plot_colors=plot_colors,
                     to_csv=True)

        #
        # Create the stat report
        #
        # report_multi_tiles([cfg['stats_results']['modes'][modes[mode]]], cfg['outputDir'])


def wave_detection(cfg, dh, display=False):
    """
    Detect potential oscillations inside dh

    :param cfg: config file
    :param dh: A3DGeoRaster, dsm - ref
    :return:

    """

    # Compute mean dh row and mean dh col
    # -> then compute the min between dh mean row (col) vector and dh rows (cols)
    res = {'row_wise': np.zeros(dh.r.shape, dtype=np.float32), 'col_wise': np.zeros(dh.r.shape, dtype=np.float32)}
    axis = -1
    for dim in res.keys():
        axis += 1
        mean = np.nanmean(dh.r, axis=axis)
        if axis == 1:
            # for axis == 1, we need to transpose the array to substitute it to dh.r otherwise 1D array stays row array
            mean = np.transpose(np.ones((1, mean.size), dtype=np.float32) * mean)
        res[dim] = dh.r - mean

        cfg['stats_results']['images']['list'].append(dim)
        cfg['stats_results']['images'][dim] = copy.deepcopy(cfg['alti_results']['dzMap'])
        cfg['stats_results']['images'][dim].pop('nb_points')
        cfg['stats_results']['images'][dim]['path'] = os.path.join(cfg['outputDir'], 'dh_{}_wave_detection.tif'.format(dim))

        georaster = A3DGeoRaster.from_raster(res[dim], dh.trans, "{}".format(dh.srs.ExportToProj4()), nodata=-32768)
        georaster.save_geotiff(cfg['stats_results']['images'][dim]['path'])
