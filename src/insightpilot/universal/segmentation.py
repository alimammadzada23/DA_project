"""Bounded, deterministic exploratory k-means with an explicit validation score."""
import numpy as np
import pandas as pd
from scipy.cluster.vq import kmeans2
from scipy.spatial.distance import cdist

from .engine import frame_of
from .profile import numeric_expression


def cluster(engine,columns,k=3):
    if not 2<=len(columns)<=8 or len(set(columns))!=len(columns): raise ValueError('Choose 2–8 distinct numeric features.')
    if not 2<=k<=8: raise ValueError('Choose 2–8 clusters.')
    for col in columns:
        engine.column(col)
        if engine.info['roles'].get(col) in ('identifier','customer','product','transaction','date'):
            raise ValueError('Identifiers and dates are not meaningful Euclidean clustering features.')
    expressions=[numeric_expression(c) for c in columns]
    projection=','.join(f'{expr} AS feature_{i}' for i,expr in enumerate(expressions))
    complete=' AND '.join(f'({expr}) IS NOT NULL' for expr in expressions)
    evidence=engine.run(f'SELECT _row_id,{projection} FROM selected WHERE {complete} ORDER BY _row_id',limit=50000,
        method={'method':'Standardized k-means','features':columns,'k':k,'seed':42,'maximum_fit_rows':50000})
    if evidence['truncated']: raise ValueError('More than 50,000 complete rows: filter or prepare entity aggregates before clustering. No silent fit sample is used.')
    data=frame_of(evidence); n=len(data)
    if n<max(20,5*k): raise ValueError('At least 20 complete rows and five per requested cluster are required.')
    x=data.iloc[:,1:].to_numpy(dtype=float); mean=x.mean(axis=0); sd=x.std(axis=0)
    if np.any(sd==0): raise ValueError('Constant clustering features must be removed.')
    z=(x-mean)/sd
    centers,labels=kmeans2(z,k,iter=50,minit='++',missing='raise',seed=42)
    unique,counts=np.unique(labels,return_counts=True)
    if len(unique)!=k or counts.min()<2: raise ValueError('Degenerate or singleton clusters; choose a different k or feature set.')
    rng=np.random.default_rng(42); indices=rng.choice(n,size=min(n,2000),replace=False)
    sampled=z[indices]; sampled_labels=labels[indices]; distances=cdist(sampled,sampled)
    silhouette=[]
    for i,label in enumerate(sampled_labels):
        own=sampled_labels==label; own_n=int(own.sum())
        if own_n<=1: silhouette.append(0.); continue
        a=float(distances[i,own].sum()/(own_n-1))
        alternatives=[float(distances[i,sampled_labels==other].mean()) for other in unique if other!=label and (sampled_labels==other).any()]
        b=min(alternatives)
        silhouette.append((b-a)/max(a,b) if max(a,b)>0 else 0.)
    _,_,axes=np.linalg.svd(z,full_matrices=False)
    points=z@axes[:2].T
    assignments=pd.DataFrame({'_row_id':data._row_id,'cluster':labels+1,'projection_1':points[:,0],'projection_2':points[:,1]})
    centroids=pd.DataFrame(centers*sd+mean,columns=columns); centroids.insert(0,'cluster',range(1,k+1))
    return {'data':assignments,'centroids':centroids,'silhouette':float(np.mean(silhouette)),
        'validation_sample_rows':len(indices),'complete_rows':n,'excluded_incomplete_rows':engine.count()-n,'evidence':evidence,
        'limitations':'Exploratory standardized Euclidean k-means, fixed seed; clusters are model-generated groups, not verified personas or business truth. '
        'The silhouette score uses at most 2,000 deterministically sampled fitted rows; it is an internal score, not external validation. '
        'Confirm one independent entity per row; repeated transactions can cluster activity instead of customers. '
        'Features, outliers and k affect results. The two-dimensional SVD view is a projection, not the full clustering geometry.'}
