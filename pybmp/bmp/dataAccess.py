import os
import itertools

try:
    import pyodbc
except ImportError:
    pyodbc = None

import numpy as np
import pandas
from pandas.io import sql

from . import info
from .. import utils
from ..core import features


__all__ = [
    'defaultFilter',
    'Database',
    'Table',
    'Parameter'
]


def _process_screening(screen_val):
    if screen_val.lower().strip() in ['inc', 'yes']:
        return 'yes'
    elif screen_val.lower().strip() in ['exc', 'no']:
        return 'no'
    else:
        msg = 'invalid screening value ({0}) found'.format(screen_val)
        raise ValueError(msg)

def _process_sampletype(sampletype):
    if "grab" in sampletype.lower():
        return "grab"
    elif "composite" in sampletype.lower():
        return "composite"
    else:
        return "unknown"


def _filter_index(row, levels, values):
    output = True
    for lvl, val in zip(levels, values):
        if np.isscalar(val):
            if row[lvl] != val:
                output= False
        else:
            if row[lvl] not in val:
                output = False

    return output


def _check_station(station):
    if station in ['reference', 'subsurface']:
        raise NotImplementedError

    if station not in ['inflow', 'outflow']:
        raise ValueError('`station` must be "inflow" or "outflow"')


def _check_levelnames(levels):
    msg = 'valid levels are "category", "site", "bmp"'
    for lvl in levels:
        if lvl not in ['category', 'site', 'bmp', 'parameter', 'sampletype', 'epazone', 'state', 'paramgroup']:
            raise ValueError(msg)


def defaultFilter(dataframe, levelname='bmp', minElements=3, minGroups=3):
    # name and position of BMP (study) ID in the index
    levelnumber = np.nonzero(np.array(dataframe.index.names) == levelname)[0][0]

    # determine the number of studies
    elem_counts = dataframe.groupby(level=levelname).size()
    good_groups = elem_counts[elem_counts >= minElements]
    data = dataframe.select(lambda x: x[levelnumber] in good_groups)
    include = good_groups.shape[0] >= minGroups
    return data, include


class Database(object):
    '''
    Top-level object/entry point for International BMP Database analysis

    Parameters
    ----------

    filename : string
        CSV file or MS Access database containing the data.

    dbtable : optional string (default = 'pybmp_flatfile')
        Table in the MS Access database storing the data for analysis.
        Only used if `usingdb` is True.

    catanalysis : optional bool (default = False)
        Toggles the filtering for data that have been approved for BMP
        Category-level analysis

    Attributes
    ----------

    self.dbfile : string
        Full path to the database file.

    self.driver : string
        ODBC-compliant Microsoft Access driver string.

    self.category_type : string
        See Input section.

    self.usingdb : bool
        See Input section.

    self.excluded_parameters : list of string or None
        See `parametersToExclude` in Input section.

    self.data : pandas DataFrame
        DataFrame of all of the data found in the DB or CSV file.

    Methods
    -------

    (see individual docstrings for more info):

    - self.connect
    - self.redefineBMPCategory
    - self.convertTablesToCSV
    - self.getAllData
    - self.getGroupData

    '''

    def __init__(self, filename, dbtable=None, sqlquery=None,
                 catanalysis=False):
        self.file = filename
        self.usingdb = os.path.splitext(self.file)[1] in ['.accdb', '.mdb']
        self.catanalysis = catanalysis

        # property initialization
        self.__data_fromdb = None
        self.__data_cleaned = None
        self._data = None
        if self.usingdb:
            self._sqlquery = sqlquery
            self._dbtable = dbtable
            self.driver = r'{Microsoft Access Driver (*.mdb, *.accdb)}'
        else:
            self._sqlquery = None
            self._dbtable = None
            self.driver = None

    @property
    def dbtable(self):
        return self._dbtable
    @dbtable.setter
    def dbtable(self, value):
        self._dbtable = value

    @property
    def sqlquery(self):
        if self.dbtable is not None:
            self._sqlquery = "select * from [{}]".format(self.dbtable)
        elif self._sqlquery is None:
            self._sqlquery = (
                "select\n"
                "    [src].[Analysis_Category] as [category],\n"
                "    [src].[BMP Cat Code] as [bmpcat],\n"
                "    [src].[TBMPT 2009] as [bmptype],\n"
                "    [src].[EPA Rain Zone] as [epazone],\n"
                "    [src].[State] as [state],\n"
                "    [src].[Country] as [country],\n"
                "    [src].[SITENAME] as [site],\n"
                "    [src].[BMPName] as [bmp],\n"
                "    [src].[PDF ID] as [PDFID],\n"
                "    [src].[WQID],\n"
                "    [src].[MSNAME] as [monitoringstation],\n"
                "    [src].[Storm #] as [storm],\n"
                "    [src].[SAMPLEDATE] as [sampledate],\n"
                "    [src].[SAMPLETIME] as [sampletime],\n"
                "    [src].[Group] as [paramgroup],\n"
                "    [src].[Analysis Sample Fraction] as [fraction],\n"
                "    [src].[WQX Parameter] as [raw_parameter],\n"
                "    [src].[Common Name] as [parameter],\n"
                "    [src].[WQ UNITS] as [wq_units],\n"
                "    [src].[WQ Analysis Value] as [wq_value],\n"
                "    [src].[QUAL] as [wq_qual],\n"
                "    [src].[Monitoring Station Type] as [station],\n"
                "    [src].[SGTCodeDescp] as [watertype],\n"
                "    [src].[STCODEDescp] as [sampletype],\n"
                "    [src].[Use in BMP WQ Analysis] as [wqscreen],\n"
                "    [src].[Use in BMP Category Analysis] as [catscreen],\n"
                "    [src].[Infl_Effl_Balance] as [balanced]\n"
                "from [bWQ BMP FlatFile BMP Indiv Anal_Rev 10-2014] as [src]\n"
                "where [src].[Common Name] is not null\n"
                "order by\n"
                "    [src].[TBMPT 2009],\n"
                "    [src].[CATEGORY],\n"
                "    [src].[SITENAME],\n"
                "    [src].[BMPName],\n"
                "    [src].[Storm #],\n"
                "    [src].[SAMPLEDATE],\n"
                "    [src].[Common Name],\n"
                "    [src].[WQX Parameter],\n"
                "    [src].[Analysis Sample Fraction],\n"
                "    [src].[Monitoring Station Type];"
            )
        return self._sqlquery
    @sqlquery.setter
    def sqlquery(self, value):
        self._sqlquery = value

    @property
    def _data_fromdb(self):
        if self.__data_fromdb is None:
            if self.usingdb:
                # SQL query text, execution, data retrieval
                with self.connect() as cnn:
                    self.__data_fromdb = sql.read_sql(self.sqlquery, cnn)

            else:
                self.__data_fromdb = pandas.read_csv(self.file, encoding='utf-8')

        return self.__data_fromdb

    def _cleanup_data(self):

        data = self._data_fromdb.copy()
        if self.usingdb:
            # clean up the flags (remove leading and trailing spaces:
            data['wq_qual'] = data['wq_qual'].str.strip()

            # WQ results need to be multiplied by 2 if the qual is a certain value
            factor_2_quals = ['U', 'UK', 'UA', 'UC', 'K']
            ND_factors = data['wq_qual'].apply(lambda x: 2 if x in factor_2_quals else 1)
            data['wq_value'] *= ND_factors

            # reset all of the non-detect flags to something universal ("ND")
            ## UJ -> ND if res < DL, else ,=
            ND_flags = ['U', 'UJ', 'UA', 'UI', 'UC', 'UK', 'K']
            nondetects = data['wq_qual'].str.strip().isin(ND_flags)
            data.loc[nondetects, 'wq_qual'] = 'ND'

            # reset all detect-flags to "="
            detects = data['wq_qual'] != 'ND'
            data.loc[detects, 'wq_qual'] = '='

            # rename columns:
            rename_columns = {
                'wq_qual': 'qual',
                'wq_value': 'res',
                'wq_units': 'units',
                'Fraction': 'fraction',
                'raw_parameter': 'general_parameter',
                'category': 'category'
            }
            drop_columns = ['monitoringstation']
            data = (
                data.rename(columns=rename_columns)
                    .drop(drop_columns, axis=1)
                    .dropna(subset=['res'])
            )
            # process screening values:
            data['wqscreen'] = data['wqscreen'].apply(_process_screening)
            data['catscreen'] = data['catscreen'].apply(_process_screening)
            data['station'] = data['station'].str.lower()
            data['sampletype'] = data['sampletype'].apply(_process_sampletype)
            data['sampledatetime'] = data.apply(utils.makeTimestamp, axis=1)

            # screen the data
            if self.catanalysis:
                qrystring = (
                    "catscreen == 'yes' and "
                    "bmpcat    != 'EXC' and "
                    "balanced  == '='"
                )
                data = data.query(qrystring)

            # normalize the units
            data = utils.normalize_units2(data, info.getNormalization,
                                          info.getConversion, info.getUnits,
                                          paramcol='parameter', rescol='res',
                                          unitcol='units', dlcol=None)

        return data

    def _group_data(self):
        # columns to be the index
        row_headers = [
            'category', 'epazone', 'state', 'site', 'bmp',
            'station', 'storm', 'sampletype', 'watertype',
            'paramgroup', 'units', 'parameter', 'wqscreen',
            'catscreen', 'balanced', 'PDFID', 'WQID'
        ]

        # group the data based on the index
        agg_rules = {'res': 'mean', 'qual': 'min', 'sampledatetime': 'min'}

        return self._data_cleaned.groupby(by=row_headers).agg(agg_rules)

    @property
    def _data_cleaned(self):
        if self.__data_cleaned is None:
            self.__data_cleaned = self._cleanup_data()
        return self.__data_cleaned

    @property
    def data(self):
        if self._data is None:
            self._data = self._group_data()
        return self._data

    @property
    def index(self):
        return {name: level for level, name in enumerate(self.data.index.names)}

    def connect(self, cmd=None, commit=False, filepath=None):
        '''
        Connects to the database using pyodbc. Executes a command if provided.

        Parameters
        ----------
        cmd : optional string or None (default)
            SQL statement that will be executed (see Notes).

        commit : optional bool (default = False)
            Toggles if the changes to the database executed with `cmd`
            should be save. Be carefule. You could delete everything

        Returns
        -------
        cnn : pyodbc connection object

        Notes
        ------
         - It's recommended to not use the `cmd` argument to retrieve data.
           In fact, it's impossible. If you need to execute a custom
           selection query it's recommended to use the function to create
           the connection object and the pass that to
           pandas.io.sql.read_frame (see Examples).
         - This function is primarily used internally to select large
           amounts of data when instantiating the `Database` object.
           It's probably best to use pandas selection methods on the
           `data` attribute to isolated specific records for a
           particular analysis.

        Examples
        -------
        >>> from pandas.io import sql
        >>> import bmp
        >>> db = bmp.dataAccess.Database()
        >>> myCmd = "SELECT * FROM myTable"
        >>> with db.connect() as cnn:
           ...: data = sql.read_frame(myCmd, cnn)
        >>> print(data.head())

        '''

        if os.path.splitext(self.file)[1] not in ['.accdb', '.mdb']:
            raise ValueError('Datasource is not an MS Access database')

        # connection string
        connection_string = r'Driver=%s;DBQ=%s' % (self.driver, self.file)

        # make the connection and cursor
        cnn = pyodbc.connect(connection_string)

        # execute commands if provided
        if cmd is not None:
            cur = cnn.cursor()

            try:
                cur.execute(cmd)

                # commit if requested
                if commit:
                    cnn.commit()

            # raise whatever exception we encoutered
            except:
                raise
            # be sure to close the cursor
            finally:
                cur.close()

        return cnn

    def convertTableToCSV(self, tablename, filepath=None):
        '''
        Converts all relevant tables in the DB to CSV files
        '''
        if not self.usingdb:
            raise NotImplementedError('`Database` source is not an Access Database')
        if filepath is None:
            filepath = 'bmp/data/{0}.csv'
        cmd = "select * from [{0}]".format(tablename)
        with self.connect() as cnn:
            sql.read_frame(cmd, cnn).to_csv(filepath, index=False, encoding='utf-8')

    def selectData(self, astable=False, name=None, useTex=False, **kwargs):
        '''
        Select data from the database.

        Input
        -----
        astable : bool (default = False)
            If True, returns a bmp.Table object. Otherwise, returns a
            pandas.DataFrame.
        name : string or None (default)
            If provided and astable is True, this is passed on as the
            name of the bmp.Table object.
        Optional kwargs:
            You can additioanlly pass keyword arguments that define
            selection criteria on the data. Values can be either strings
            (single values) or lists of strings (multiple values).
            Valid Keys are:
                'category', 'site', 'bmp', 'storm', 'paramgroup',
                'units', 'parameter', 'sampletype', 'epazone', 'state',
                'wqscreen', and 'catscreen'

        Returns
        -------
        pandas.DataFrame or bmp.Table per `astable`

        Examples
        --------
        >>> db = bmp.Database(my_database_path)
        >>> table = db.selectData(astable=True,
            category=['Wetland Basin', 'Bioretention'],
            paramgroup='Nutrients')

        '''
        good_keys = [
            'category', 'site', 'bmp', 'storm',
            'paramgroup', 'units', 'parameter',
            'sampletype', 'epazone', 'state',
            'wqscreen', 'catscreen'
        ]
        level_dict = {}
        for key in kwargs.keys():
            if key not in good_keys:
                raise ValueError("filtering by %s not supported" % key)

            level_dict[self.index[key]] = kwargs[key]

        selection = lambda row: _filter_index(row, level_dict.keys(), level_dict.values())
        data = self.data.select(selection)
        if astable:
            return Table(data, name=name, useTex=useTex)
        else:
            return data


class Table(object):
    '''
    Object representing a table in the BMP Database. You can, but
    /shouldn't/ instantiate this yourself. Instead, it is recommended
    to use one of the methods of the `Database` object to create your
    `Table`.

    Parameters
    ----------

    data : pandas.Dataframe
        The subset of data created from the `data` attribute
        of a `Database` object.
    bmpcats : dict
        A dictionary in the form of [bmp code]: [bmp description].
        This is best taken directly from the `bmp_cats` attribute
        of the source `Database` object.
    name : optional string or None (default)
        Name of the `Table`. Useful in summarization routines, but
        not necessary.

    Attributes
    ----------

    name : string
        name of the table
    data : pandas dataframe
        pivot table of the inflow/outflow
            results and quals
    parameters : list of Parameters
        Parameter objects for each
            unique parameter-unit combination.
    columns : array of strings
        names of the columns in self.data
    index : array of strings
        names of the index levels in self.data
    bmp_cats : array of strings
        BMP Categories present in self.data

    Methods
    -------

    getData
    redefineIndexLevel
    redefineBMPCategory
    transformParameters
    unionParamsWithPreference
    getLocations
    getDatasets

    '''

    def __init__(self, dataframe, name=None, useTex=False):
        # basic stuff
        self.data = dataframe
        self.name = name
        self._parameters = None
        self._useTex = useTex

    @property
    def useTex(self):
        return self._useTex
    @useTex.setter
    def useTex(self, value):
        self._useTex = value

    # XXX: also codes
    @property
    def bmp_categories(self):
        return self.index_values('category')

    @property
    def parameters(self):
        self._parameters = self._get_parameters()
        return self._parameters

    @property
    def parameter_lookup(self):
        return {p.name: p for p in self.parameters}

    @property
    def index(self):
        return {name: level for level, name in enumerate(self.data.index.names)}

    def index_values(self, levelname):
        '''
        Returns the unique values of a level of `self.data`'s index

        Input
            levelname : string

        '''
        return self.data.index.get_level_values(levelname).unique()

    def _get_parameters(self):
        '''
        Looks at the dataframe `tabledata` (from `getTableData`) and returns a list
        of Parameter objects for each unique parameter-unit combination.

        Ideally, there should only be one unit for each parameter.

        Input:
            usingdb (bool, default False) : toggles reading from the database or a
                local csv file
            csvfile (string, default 'bmp/bmpcats.csv') : path and filename to the
                datafile

        Returns:
            parameters (list of Parameter objects)
        '''
        # groups the data by parameter, tex, and units
        parameter_unit_levels = ['parameter', 'units']
        paramgroups = self.data.groupby(level=parameter_unit_levels)

        # pull out any one of the groups and the get the index values
        paramunit_df = paramgroups.nth(0)
        param_index = paramunit_df.index.get_level_values('parameter')
        if not param_index.is_unique:
            raise utils.DataError('dataframe does not have consistent units')

        # initalize the results list
        parameters = []
        for row in paramunit_df.index:
            basic_param = row[self.index['parameter']]
            basic_unit = info.getUnits(basic_param)
            if self.useTex:
                p = features.Parameter(
                    name=info.getTexParam(basic_param),
                    units=info.getTexUnit(basic_unit)
                )
            else:
                p = features.Parameter(name=basic_param, units=basic_unit)

            parameters.append(p)

        return parameters

    def getData(self, parametername, bmpCat, paired=False):
        '''
        Method to return a cross-section of self.data for a
        parameter and BMP category.

        Input:
            parameter (string) : parameter we're looking out
            bmpCat (string) : abbreviated code for the BMP category
                (see self.bmpCats)
            paired (bool, default False) : if True, missing values will
                be dropped (leaving only paired data) and `diff` and
                `logdiff` columns will be added to the database.

        Writes:
            None

        Returns:
            selection (pandas dataframe) : subset of self.data containing only
                records for the given parameter and BMP category

        Typcial Usage:
            >>> table = bmp.dataAccess.Table(metals)
            >>> for parameter in table.parameters:
            >>>     for bmpcat in table.bmpCats:
            >>>         data = table.getData(parameter, bmpcat)
            >>>         # do stuff with data

        '''
        raise NotImplementedError
        # # error handling to make sure value parameters and bmps are passed
        # if not parametername in self.parameter_lookup.keys():
        #     raise ValueError("parameter %s not available" % parametername)
        # elif not bmpCat in self.bmp_categories:
        #     raise ValueError("bmp category %s not available" % bmpCat)

        # # cross-section the data
        # selection = self.data.xs([bmpCat, parametername],
        #                       level=['category', 'parameter'])

        # # created a paired dataset, if necessary
        # if paired:
        #     selection = selection.dropna(axis=0, subset=[('Inflow', 'res'),
        #                                                  ('Outflow', 'res')])
        #     selection['diff'] = selection.Inflow.res - selection.Outflow.res
        #     selection['logdiff'] = np.log10(selection.Inflow.res) - \
        #         np.log10(selection.Outflow.res)

        # return selection

    def redefineIndexLevel(self, levelname, value, criteria, dropold=True):
        '''
        Redefine a selection of BMPs into another or new category
        Input:
            levelname : string
                The name of the index level that needs to be modified.
                (see `Database.index`)

            value : string or int
                The replacement value for the index level.

            critera : function/lambda expression
                This should return True/False in a manner consitent with the
                `.select()` method of a pandas dataframe. See that docstring
                for more info.

            dropold : optional bool (defaul is True)
                Toggles the replacement (True) or addition (False) of the data
                of the redefined BMPs into the the `data` dataframe.

        Returns:
            None

        Notes:
            The standard dataframe present in `Database.data` has the following
            indicies:
                Level - Name
                    0 - category (determined by `category_type`)
                    1 - epazone
                    2 - state
                    3 - site
                    4 - bmp
                    5 - storm
                    6 - sampletype
                    7 - paramgroup
                    8 - units
                    9 - parameter
            So if you were creating a selection based on a set of Site IDs, your
            lambda expression would look like this:
                >>> criteria = lambda row: row[1] in my_site_id_list

        Example:
            >>> # import and create `Database` object
            >>> import bmp
            >>> db = bmp.dataAccess.Database()
            >>> # move tree box planters into their own EPA Zone
            >>> bmpcats = [-1098775618, 95902823, 1053525776, 1495211473]
            >>> criteria = lambda row: row[3] in TB_bmps
            >>> db.redefineIndexLevel('epazone', 9999, criteria, dropold=True)
            >>> print(db.data.index.get_level_values('epazone').unique()) # that it worked
        '''
        self.data = utils.redefineIndexLevel(
            self.data, levelname, value,
            criteria=criteria, dropold=dropold
        )

    def redefineBMPCategory(self, bmpname, criteria, dropold=True):
        '''
        Redefine a selection of BMPs into another or new category
        Input:
            bmpcode : string
                The new abbreviation/code for your new BMP category that will
                appear in the dataframe.

            bmpname : string
                The longer-form name/description of the BMP category that will
                be created.

            critera : function/lambda expression
                This should return True/False in a manner consitent with the
                `.select()` method of a pandas dataframe. See that docstring
                for more info.

            dropold : optional bool (defaul is True)
                Toggles the replacement (True) or addition (False) of the data
                of the redefined BMPs into the the `data` dataframe.

        Returns:
            None

        Notes:
            The standard dataframe present in `Database.data` has the following
            indicies:
                Level - Name
                    0 - category (determined by `category_type`)
                    1 - epazone
                    2 - state
                    3 - site
                    4 - bmp
                    5 - storm
                    6 - sampletype
                    7 - paramgroup
                    8 - units
                    9 - parameter
            So if you were creating a selection based on a set of Site IDs, your
            lambda expression would look like this:
                >>> criteria = lambda row: row[1] in my_site_id_list

        Example:
            >>> # import and create `Database` object
            >>> import bmp
            >>> db = bmp.dataAccess.Database()
            >>> # replace tree box planters original BMP category (MD)
            >>> # with "TreeBox"
            >>> TB_bmps = [-1098775618, 95902823, 1053525776, 1495211473]
            >>> criteria = lambda row: row[3] in TB_bmps
            >>> db.redefineBMPCategory('TB', 'Tree box planter', criteria)
            >>> print(db.data.index.get_level_values('category').unique()) # that it worked
        '''
        self.data = utils.redefineIndexLevel(
            self.data, 'category', bmpname,
            criteria=criteria, dropold=dropold
        )

    def transformParameters(self, existingparams, newparam, resfxn, qualfxn, newunits, indexMods=None):
        '''
        Apply an arbitrary transformation to a parameter in `table.data`. For
        example, converting pH into H+ concentration.

        Input:
            existingparams : list of strings
                List of the existing parameters that will be used to compute
                the new values

            newparam : string
                Name of the new parameter to be generated

            resfxn : function
                This is function (or lambda expression) that will compute or
                select the value of `newparam` based on the values of
                `existingparams`. Function must assume to be operating on a
                pandas.DataFrame row with the elements of `existingparams`
                in columns.

            qualfxn : function
                Same as `resfxn`, but for selecting the final qualifier of the
                `newparam` results

            newunits : string
                Units of the newly computed values

            indexMods : optional dict (keys = index level names)
                Dictionary of index level name whose values are the new values
                of those levels where parameter=`newparam`.

        Returns:
            None - operates on `table.data` in place.

        Example:
            >>> db = bmp.dataAccess.Database(file='bmp/data/data_for_pybmp.csv')
            >>> table = bmp.dataAccess.Table('General', db)
            >>> table.transformParameters(['pH'], 'protons',
                  lambda x, junk: utils.pH2concentration(x[('res', 'pH')]),
                  lambda x, junk: x[('qual', 'pH')], 'mg/L')
        '''
        if np.isscalar(existingparams):
            existingparams = [existingparams]
        for param in existingparams:
            if param not in self.parameter_lookup.keys():
                raise ValueError("Parameter %s is not in this dataset" % param)

        pindex = self.index['parameter']
        selection = self.data.query("parameter in {}".format(existingparams))

        # put the station into the row index and pivot the param into columns
        #selection = selection.stack(level='station')
        selection = selection.unstack(level='parameter')

        # compute the right values
        selection[('qual', newparam)] = selection.apply(qualfxn, axis=1,
                                                  args=existingparams)
        selection[('res', newparam)] = selection.apply(resfxn, axis=1,
                                                 args=existingparams)

        # keep only the combined data
        selection = selection.select(lambda col: col[1] == newparam, axis=1)

        # station goes back in into columns, parameters into rows
        #selection = selection.unstack(level='station')
        selection = selection.stack(level='parameter')

        selection.index = selection.index \
                                   .swaplevel('parameter', 'catscreen') \
                                   .swaplevel('parameter', 'wqscreen')

        # get the column indices in the right order
        #selection.columns = selection.columns.swaplevel('quantity', 'station')

        # check on indexMods
        if indexMods is None:
            indexMods = {}

        # check indexMods type
        if indexMods is not None:
            if not isinstance(indexMods, dict):
                raise ValueError('indexMods must be a dictionary')

            # add the units into indexMod, apply all changes
            indexMods['units'] = newunits
            for levelname, value in indexMods.items():
                selection = utils.redefineIndexLevel(
                    selection,
                    levelname,
                    value,
                    criteria=None,
                    dropold=True
                )

        if newunits not in info.units.keys():
            info.units = info.addUnit(newunits, 1)

        if newparam not in info.parameters.keys():
            info.parameters = info.addParameter(newparam, newunits)

        # return the *full* dataset (preserving original params)
        self.data = pandas.concat([self.data, selection])

    def unionParamsWithPreference(self, existingparams, newparam, newunits):
        '''
        Looks as instances of two different analytes, picks the best one, and
        the appends a new row with the preferred result under a new parameter
        name. The best example of this is taking NO3+NO2 and NO3 data, and
        "unioning" them to get NOx data -- NO3+NO2 is the best to use, but if
        it's not available, fall back to NO3 since NO2 is typically small.

        Input:
            existingparams : list of string
                List of the parameters you wish to merge (currently limited
                to 2)

            newparam : string
                Name of the new parameter you're creating (e.g., NOx in the
                example above)

            newunits : string
                Units of the new value

        Returns:
            None - operates on `table.data` in place

        Example:
            >>> db = bmp.dataAccess.Database(file='bmp/data/data_for_pybmp.csv')
            >>> table = bmp.dataAccess.Table('Nutrients', db)
            >>> nitro_components = [
                'Nitrogen, Nitrite (NO2) + Nitrate (NO3) as N',
                'Nitrogen, Nitrate (NO3) as N'
            ]
            >>> nitro_combined = 'Nitrogen, NOx as N'
            >>> table.unionParamsWithPreference(nitro_components, nitro_combined, 'mg/L')
        '''
        if len(existingparams) != 2:
            raise NotImplementedError('existingparams must be a sequence of length = 2')

        # function to return the right column of qualifiers
        def returnFiniteQual(row, preferred, secondary):
            if isinstance(row[('qual', preferred)], str):
                return row[('qual', preferred)]
            else:
                return row[('qual', secondary)]

        # function to return the right column of results
        def returnFiniteRes(row,  preferred, secondary):
            if np.isfinite(row[('res', preferred)]):
                return row[('res', preferred)]
            else:
                return row[('res', secondary)]

        self.transformParameters(existingparams, newparam, returnFiniteRes, returnFiniteQual, newunits)

    @staticmethod
    def _make_name(indexvals):
        '''
        helper to create the name of a Dataset based on the
        attributes used to slice up the Table
        '''
        namevals = []
        for val in indexvals:
            namevals.append(str(val))
        return '_'.join(namevals)

    def _get_dataset(self, selection, filterfxn=None, absmin=0):
        influent = self._get_location(selection, 'inflow', filterfxn=filterfxn, absmin=absmin)
        effluent = self._get_location(selection, 'outflow', filterfxn=filterfxn, absmin=absmin)
        if influent is not None and effluent is not None:
            dataset = features.Dataset(influent, effluent)
        else:
            dataset = None

        return dataset

    def _get_location(self, selection, station, filterfxn=None, absmin=0):
        selection = self.data.select(selection)
        data = selection[station.title()].dropna(subset=['res', 'qual'])

        if data.shape[0] >= absmin:
            loc = features.Location(data, station_type=station)

            # filter the data if necessary
            if filterfxn is not None:
                loc.applyFilter(filterfxn)

        else:
            loc = None

        return loc

    def getLocations(self, station, *levels, **kwargs):
        '''
        Returns a list of core.features.Location objects from queried out from
        `Table.data` and the relevant metadata about  what sorts of info make
        up those datasets. At a minimum, datasets will be divded up by
        parameter. Can additionally cut up the data by 'site', bmp', and
        'category' (all other possible index levels are not very meaningful).

        Input:
            station : string ['inflow', 'outflow']
                Monitoring station desired

            *levels : strings
                This function accepts and arbitary number of string arguments
                the specify the index levels that define a unique dataset in
                addition to 'parameter'. At a bare minimum, it is recommended
                to use 'category' as well. See examples below.

            filterfxn : optional function or None (default)
                A function designed to remove data and set the `include`
                attribute of the `Dataset.influent` and `Dataset.effluent`
                objects based on user-defined criteria. For example,
                `dataAccess.defaultFilter` will removed any studies with less
                than 3 datapoints from a `Location` object and set the
                `include` attribute to False if their are fewer than 3 remaing
                studies. This is a very advanced feature.

            absmin : optional int (default = 3)
                The absolute minimum number if datapoints required to attempt
                to make the `Location` objects. It is not recommended to use
                anything lower than 3.

            showprogress : option bool (default = False)
                If True, an ASCII progress bar will be displayed as datasets
                are created.

        Returns:
            datasets : list of Location objects.

        Example:
            >>> import bmp
            >>> db = bmp.dataAccess.Database('data.csv', 'bmpcats.csv')
            >>> table = bmp.dataAccess.Table('Metals', db) # <-- this is wrong (TODO)
            >>> locations = table.getLocations('category', 'epazone')
            >>> print(locations[0].definition.keys())

        '''
        _check_station(station)
        _check_levelnames(levels)
        filterfxn = kwargs.pop('filterfxn', None)
        showprogress = kwargs.pop('showprogress', False)

        grouplevels = ['parameter', 'station']
        grouplevels.extend(levels)

        data = self.data.query("station in ['inflow', 'outflow']")
        datagroups = data.groupby(level=grouplevels)

        pbar = utils.ProgressBar(datagroups)

        locations = []
        for locnum, (key, locdata) in enumerate(datagroups):
            # select the data for an individual dataset
            locdata = locdata.dropna(subset=['res', 'qual'])

            loc = features.Location(locdata, station_type=key[1].lower())
            if filterfxn is not None:
                loc.applyFilter(filterfxn, **kwargs)

            if np.isscalar(key):
                key = [key]

            defn = dict(zip(grouplevels, key))
            defn['parameter'] = self.parameter_lookup[defn['parameter']]
            locations.append({
                'location': loc,
                'name': self._make_name(key),
                'definition': defn
            })

            if showprogress:
                pbar.animate(locnum)

        return locations

    def getDatasets(self, *levels, **kwargs):
        '''
        Returns a list of core.features.Dataset objects from queried out from
        `Table.data` and the relevant metadata about  what sorts of info make
        up those datasets. At a minimum, datasets will be divded up by
        parameter. Can additionally cut up the data by 'site', bmp', and
        'category' (all other possible index levels are not very meaningful).

        Input:
            *levels : strings
                This function accepts and arbitary number of string arguments
                the specify the index levels that define a unique dataset in
                addition to 'parameter'. At a bare minimum, it is recommended
                to use 'category' as well. See examples below.

            filterfxn : optional function or None (default)
                A function designed to remove data and set the `include`
                attribute of the `Dataset.influent` and `Dataset.effluent`
                objects based on user-defined criteria. For example,
                `dataAccess.defaultFilter` will removed any studies with less
                than 3 datapoints from a `Location` object and set the
                `include` attribute to False if their are fewer than 3 remaing
                studies. This is a very advanced feature.

            absmin : optional int (default = 3)
                The absolute minimum number if datapoints required to attempt
                to make the `Location` objects. It is not recommended to use
                anything lower than 3.

            showprogress : option bool (default = False)
                If True, an ASCII progress bar will be displayed as datasets
                are created.

        Returns:
            datasets : list of Dataset objects.

        Example:
            >>> import bmp
            >>> db = bmp.dataAccess.Database('data.csv', 'bmpcats.csv')
            >>> table = bmp.dataAccess.Table('Metals', db) # <-- this is wrong (TODO)
            >>> datasets = table.getDatasets('category', 'epazone')
            >>> print(datasets[0].definition.keys())

        '''
        _check_levelnames(levels)
        filterfxn = kwargs.pop('filterfxn', None)
        showprogress = kwargs.pop('showprogress', False)

        grouplevels = ['parameter']
        grouplevels.extend(levels)

        datagroups = self.data.groupby(level=grouplevels)

        pbar = utils.ProgressBar(datagroups)#, labelfxn=lambda g: print(g[0]))

        datasets = []
        for dsnum, (key, dsdata) in enumerate(datagroups):
            # select the data for an individual dataset

            inflow = dsdata.xs("inflow", level='station')
            infl = features.Location(
                inflow.dropna(subset=['res', 'qual']),
                station_type='inflow', include=True
            )

            outflow = dsdata.xs("outflow", level='station')
            effl = features.Location(
                outflow.dropna(subset=['res', 'qual']),
                station_type='outflow', include=True
            )

            if filterfxn is not None:
                if infl is not None and infl.hasData:
                    infl.applyFilter(filterfxn, **kwargs)
                if effl is not None and effl.hasData:
                    effl.applyFilter(filterfxn, **kwargs)

            ds = features.Dataset(infl, effl)

            if np.isscalar(key):
                key = [key]

            ds.definition = dict(zip(grouplevels, key))
            if self.useTex:
                param_name = info.getTexParam(ds.definition['parameter'])
            else:
                param_name = ds.definition['parameter']

            ds.definition['parameter'] = self.parameter_lookup[param_name]

            datasets.append(ds)
            if showprogress:
                pbar.animate(dsnum + 1)

        return datasets

    def getDatasetCollection(self):
        pass


class Parameter(object):
    '''
    Class representing a single parameter

    Input:
        name : string
            name of the parameter

    Attributes:
        name : string
            standard name of the parameter found in the DB

        tex : string
            LaTeX-ready version of `name`

        units : string
            units of measure for the parameter

        paramunit : string
            decently formatted combo of `name` and `units`

    Methods:
        None
    '''

    def __init__(self, name):
        self.name = name
        self.tex = info.getTexParam(self.name)
        self.std_units = info.getUnits(self.name)
        self.units = info.getTexUnit(self.std_units)

    def paramunit(self, usetex=True, usecomma=False):
        '''
        Creates a string representation of the parameter and units

        Input:
            usetex : optional bool (default is True)
                Toggles the use of plain text or LaTex for the
                output.

            usecomma : optional boot (default is False)
                Toggles the format of the `paramunit` attribute...
                If True:
                    self.paramunit = <parameter>, <unit>
                If False:
                    self.paramunit = <parameter> (unit)
        '''
        if usetex:
            p = self.tex
            u = self.units
        else:
            p = self.name
            u = self.std_units

        if usecomma:
             paramunit = '%s, %s' % (p.replace("&", "\&"), u)
        else:
            paramunit = '%s (%s)' % (p.replace("&", "\&"), u)

        return paramunit

    def __repr__(self):
        return "<openpybmp Parameter object>\n" + self.paramunit(usetex=False, usecomma=False)

    def __str__(self):
        return "<openpybmp Parameter object>\n" + self.paramunit(usetex=False, usecomma=False)
