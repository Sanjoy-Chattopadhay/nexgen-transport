import { lazy, Suspense, useMemo } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import Sidebar from './Sidebar';
import SectionTabs from './SectionTabs';
import TripTabs from './TripTabs';
import ScopeNotice from './ScopeNotice';
import NotFound from './NotFound';
import { Spinner } from './ui';
import { ACTIVE_THEME, ThemeContext, switchTheme } from './theme';
import { TenantProvider } from './tenant';
import { tripFromPath } from './nav';
import { ConsignorProvider } from '../modules/analytics/context/ConsignorContext';
import { TripClassProvider, useTripClass } from '../modules/analytics/context/TripClassContext';
import { DrillDownProvider } from '../modules/analytics/context/DrillDownContext';
import { parseConsignorPath } from '../modules/analytics/lib/consignor';
import { FreshnessProvider } from '../modules/geofence/lib/freshness';

// ---- analytics module (fleet KPIs, trips, analytics, ML, data) -------------
const A = {
  Dashboard: lazy(() => import('../modules/analytics/pages/Dashboard')),
  DriverList: lazy(() => import('../modules/analytics/pages/DriverList')),
  DriverDetail: lazy(() => import('../modules/analytics/pages/DriverDetail')),
  RouteList: lazy(() => import('../modules/analytics/pages/RouteList')),
  RouteDetail: lazy(() => import('../modules/analytics/pages/RouteDetail')),
  VehicleList: lazy(() => import('../modules/analytics/pages/VehicleList')),
  VehicleDetail: lazy(() => import('../modules/analytics/pages/VehicleDetail')),
  ConsigneeList: lazy(() => import('../modules/analytics/pages/ConsigneeList')),
  ConsigneeDetail: lazy(() => import('../modules/analytics/pages/ConsigneeDetail')),
  ConsignorList: lazy(() => import('../modules/analytics/pages/ConsignorList')),
  ConsignorDetail: lazy(() => import('../modules/analytics/pages/ConsignorDetail')),
  Migration: lazy(() => import('../modules/analytics/pages/Migration')),
  EtlSync: lazy(() => import('../modules/analytics/pages/EtlSync')),
  TTAData: lazy(() => import('../modules/analytics/pages/TTAData')),
  TTATripDetail: lazy(() => import('../modules/analytics/pages/TTATripDetail')),
  TTATripAnalysis: lazy(() => import('../modules/analytics/pages/TTATripAnalysis')),
  TTANetwork: lazy(() => import('../modules/analytics/pages/TTANetwork')),
  TTAWaypoints: lazy(() => import('../modules/analytics/pages/TTAWaypoints')),
  TTAHotspots: lazy(() => import('../modules/analytics/pages/TTAHotspots')),
  TTACompare: lazy(() => import('../modules/analytics/pages/TTACompare')),
  Geofence: lazy(() => import('../modules/analytics/pages/Geofence')),
  Manual: lazy(() => import('../modules/analytics/pages/Manual')),
  AnalyticsLayout: lazy(() => import('../modules/analytics/pages/analytics/AnalyticsLayout')),
  AnalyticsHub: lazy(() => import('../modules/analytics/pages/analytics/AnalyticsHub')),
  Overview: lazy(() => import('../modules/analytics/pages/analytics/Overview')),
  Trends: lazy(() => import('../modules/analytics/pages/analytics/Trends')),
  Transporters: lazy(() => import('../modules/analytics/pages/analytics/Transporters')),
  TransporterProfile: lazy(() => import('../modules/analytics/pages/analytics/TransporterProfile')),
  Safety: lazy(() => import('../modules/analytics/pages/analytics/Safety')),
  Lanes: lazy(() => import('../modules/analytics/pages/analytics/Lanes')),
  GeoMap: lazy(() => import('../modules/analytics/pages/analytics/GeoMap')),
  Heatmaps: lazy(() => import('../modules/analytics/pages/analytics/Heatmaps')),
  Fleet: lazy(() => import('../modules/analytics/pages/analytics/Fleet')),
  Distributions: lazy(() => import('../modules/analytics/pages/analytics/Distributions')),
  Explorer: lazy(() => import('../modules/analytics/pages/analytics/Explorer')),
  TransporterHub: lazy(() => import('../modules/analytics/pages/transporters/TransporterHub')),
  BestByLane: lazy(() => import('../modules/analytics/pages/transporters/BestByLane')),
  MLLayout: lazy(() => import('../modules/analytics/pages/ml/MLLayout')),
  MLHub: lazy(() => import('../modules/analytics/pages/ml/MLHub')),
  ETAPredictor: lazy(() => import('../modules/analytics/pages/ml/ETAPredictor')),
  SLAPredictor: lazy(() => import('../modules/analytics/pages/ml/SLAPredictor')),
  AnomalyScanner: lazy(() => import('../modules/analytics/pages/ml/AnomalyScanner')),
  DriverScorer: lazy(() => import('../modules/analytics/pages/ml/DriverScorer')),
  FatigueMonitor: lazy(() => import('../modules/analytics/pages/ml/FatigueMonitor')),
  DriverRecommender: lazy(() => import('../modules/analytics/pages/ml/DriverRecommender')),
  DemandForecaster: lazy(() => import('../modules/analytics/pages/ml/DemandForecaster')),
  RouteOptimizer: lazy(() => import('../modules/analytics/pages/ml/RouteOptimizer')),
  ClientForecast: lazy(() => import('../modules/analytics/pages/ml/ClientForecast')),
  ModelRegistry: lazy(() => import('../modules/analytics/pages/ml/ModelRegistry')),
};

// ---- geofence module (fences, live map, trip phases, routes vs plan) --------
const G = {
  DaySummary: lazy(() => import('../modules/geofence/pages/DaySummary')),
  LiveMap: lazy(() => import('../modules/geofence/pages/LiveMap')),
  GeofenceList: lazy(() => import('../modules/geofence/pages/GeofenceList')),
  GeofenceDetail: lazy(() => import('../modules/geofence/pages/GeofenceDetail')),
  PlantList: lazy(() => import('../modules/geofence/pages/PlantList')),
  PlantDetail: lazy(() => import('../modules/geofence/pages/PlantDetail')),
  States: lazy(() => import('../modules/geofence/pages/States')),
  TripList: lazy(() => import('../modules/geofence/pages/TripList')),
  TripDetail: lazy(() => import('../modules/geofence/pages/TripDetail')),
  VehicleList: lazy(() => import('../modules/geofence/pages/VehicleList')),
  VehicleDetail: lazy(() => import('../modules/geofence/pages/VehicleDetail')),
  TransporterList: lazy(() => import('../modules/geofence/pages/TransporterList')),
  TransporterDetail: lazy(() => import('../modules/geofence/pages/TransporterDetail')),
  LaneList: lazy(() => import('../modules/geofence/pages/LaneList')),
  LaneDetail: lazy(() => import('../modules/geofence/pages/LaneDetail')),
  DriverList: lazy(() => import('../modules/geofence/pages/DriverList')),
  DriverDetail: lazy(() => import('../modules/geofence/pages/DriverDetail')),
  Alerts: lazy(() => import('../modules/geofence/pages/Alerts')),
  Stops: lazy(() => import('../modules/geofence/pages/Stops')),
  DataQuality: lazy(() => import('../modules/geofence/pages/DataQuality')),
  Method: lazy(() => import('../modules/geofence/pages/Method')),
  Upload: lazy(() => import('../modules/geofence/pages/Upload')),
  UploadDetail: lazy(() => import('../modules/geofence/pages/UploadDetail')),
  Routes: lazy(() => import('../modules/geofence/pages/Routes')),
};

const DeveloperPage = lazy(() => import('./developer/DeveloperPage'));

/** Carriers used to live under /analytics; forward the old deep link. */
function LegacyTransporterRedirect() {
  const { name = '' } = useParams();
  return <Navigate to={`/transporters/${encodeURIComponent(name)}`} replace />;
}

/**
 * The routed pages, keyed by the trip-class epoch: changing the zonal/local
 * filter remounts the page, so every fetch on screen re-runs with the new
 * filter and no stale figure survives.
 */
function AppRoutes() {
  const { epoch } = useTripClass();
  return (
    <Suspense key={epoch} fallback={<Spinner />}>
      <Routes>
        <Route path="/" element={<A.Dashboard />} />
        <Route path="/drivers" element={<A.DriverList />} />
        <Route path="/drivers/:id" element={<A.DriverDetail />} />
        <Route path="/trips" element={<A.TTAData />} />
        <Route path="/trips/:tripNo" element={<A.TTATripDetail />} />
        <Route path="/trips/:tripNo/analysis" element={<A.TTATripAnalysis />} />
        <Route path="/routes" element={<A.RouteList />} />
        <Route path="/routes/:origin/:destination" element={<A.RouteDetail />} />
        <Route path="/vehicles" element={<A.VehicleList />} />
        <Route path="/vehicles/:id" element={<A.VehicleDetail />} />
        <Route path="/consignees" element={<A.ConsigneeList />} />
        <Route path="/consignees/:name" element={<A.ConsigneeDetail />} />
        <Route path="/consignors" element={<A.ConsignorList />} />
        <Route path="/consignors/:id" element={<A.ConsignorDetail />} />
        <Route path="/migration" element={<A.Migration />} />
        <Route path="/etl-sync" element={<A.EtlSync />} />
        {/* Back-compat for old /tta URLs */}
        <Route path="/tta" element={<A.TTAData />} />
        <Route path="/tta/:tripNo" element={<A.TTATripDetail />} />
        <Route path="/tta/:tripNo/analysis" element={<A.TTATripAnalysis />} />
        <Route path="/tta-network" element={<A.TTANetwork />} />
        <Route path="/tta-waypoints" element={<A.TTAWaypoints />} />
        <Route path="/tta-hotspots" element={<A.TTAHotspots />} />
        <Route path="/tta-compare" element={<A.TTACompare />} />
        <Route path="/geofence" element={<A.Geofence />} />

        <Route path="/analytics" element={<A.AnalyticsLayout />}>
          <Route index element={<A.AnalyticsHub />} />
          <Route path="overview" element={<A.Overview />} />
          <Route path="trends" element={<A.Trends />} />
          <Route path="safety" element={<A.Safety />} />
          <Route path="lanes" element={<A.Lanes />} />
          <Route path="geo" element={<A.GeoMap />} />
          <Route path="heatmaps" element={<A.Heatmaps />} />
          <Route path="fleet" element={<A.Fleet />} />
          <Route path="distributions" element={<A.Distributions />} />
          <Route path="explorer" element={<A.Explorer />} />
          <Route path="transporters" element={<Navigate to="/transporters/league" replace />} />
          <Route path="transporters/:name" element={<LegacyTransporterRedirect />} />
        </Route>
        {/* Inside the analytics shell: its worked example describes the selected window. */}
        <Route path="/manual" element={<A.AnalyticsLayout />}>
          <Route index element={<A.Manual />} />
        </Route>
        <Route path="/transporters" element={<A.AnalyticsLayout />}>
          <Route index element={<A.TransporterHub />} />
          <Route path="league" element={<A.Transporters />} />
          <Route path="lanes" element={<A.BestByLane />} />
          <Route path=":name" element={<A.TransporterProfile />} />
        </Route>
        <Route path="/ml" element={<A.MLLayout />}>
          <Route index element={<A.MLHub />} />
          <Route path="eta" element={<A.ETAPredictor />} />
          <Route path="sla" element={<A.SLAPredictor />} />
          <Route path="anomaly" element={<A.AnomalyScanner />} />
          <Route path="driver-scorer" element={<A.DriverScorer />} />
          <Route path="fatigue" element={<A.FatigueMonitor />} />
          <Route path="recommender" element={<A.DriverRecommender />} />
          <Route path="demand" element={<A.DemandForecaster />} />
          <Route path="route-optimizer" element={<A.RouteOptimizer />} />
          <Route path="client-forecast" element={<A.ClientForecast />} />
          <Route path="models" element={<A.ModelRegistry />} />
        </Route>

        <Route path="/geo" element={<G.DaySummary />} />
        <Route path="/geo/day/:day" element={<G.DaySummary />} />
        <Route path="/geo/live" element={<G.LiveMap />} />
        <Route path="/geo/geofences" element={<G.GeofenceList />} />
        <Route path="/geo/geofences/:siteId" element={<G.GeofenceDetail />} />
        <Route path="/geo/plants" element={<G.PlantList />} />
        <Route path="/geo/plants/:siteId" element={<G.PlantDetail />} />
        <Route path="/geo/states" element={<G.States />} />
        <Route path="/geo/trips" element={<G.TripList />} />
        <Route path="/geo/trips/:tripNo" element={<G.TripDetail />} />
        <Route path="/geo/vehicles" element={<G.VehicleList />} />
        <Route path="/geo/vehicles/:assetId" element={<G.VehicleDetail />} />
        <Route path="/geo/transporters" element={<G.TransporterList />} />
        <Route path="/geo/transporters/detail" element={<G.TransporterDetail />} />
        <Route path="/geo/routes" element={<G.Routes />} />
        <Route path="/geo/lanes" element={<G.LaneList />} />
        <Route path="/geo/lanes/detail" element={<G.LaneDetail />} />
        <Route path="/geo/drivers" element={<G.DriverList />} />
        <Route path="/geo/drivers/detail" element={<G.DriverDetail />} />
        <Route path="/geo/alerts" element={<G.Alerts />} />
        <Route path="/geo/stops" element={<G.Stops />} />
        <Route path="/geo/quality" element={<G.DataQuality />} />
        <Route path="/geo/method" element={<G.Method />} />
        <Route path="/geo/upload" element={<G.Upload />} />
        <Route path="/geo/upload/:id" element={<G.UploadDetail />} />

        <Route path="/developer" element={<DeveloperPage />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}

/** Tabs above the page: the trip workspace on a trip's pages, the section's views elsewhere. */
function PageTabs() {
  const { pathname } = useLocation();
  return tripFromPath(pathname) ? <TripTabs /> : <SectionTabs />;
}

export default function App() {
  // Consignor scope lives in the URL prefix (/consignor/:id/...). Mounting the
  // router under that basename keeps every route and link below unchanged.
  const { basename } = parseConsignorPath();
  const theme = useMemo(() => ({ theme: ACTIVE_THEME, setTheme: switchTheme }), []);
  return (
    <ThemeContext.Provider value={theme}>
      <TenantProvider>
        <FreshnessProvider>
          <BrowserRouter basename={basename}>
            <ConsignorProvider>
              <TripClassProvider>
                <DrillDownProvider>
                  <div className="flex min-h-screen bg-gray-950 text-gray-100">
                    <Sidebar />
                    <main className="flex-1 min-w-0 p-6">
                      <PageTabs />
                      <ScopeNotice />
                      <AppRoutes />
                    </main>
                  </div>
                </DrillDownProvider>
              </TripClassProvider>
            </ConsignorProvider>
          </BrowserRouter>
        </FreshnessProvider>
      </TenantProvider>
    </ThemeContext.Provider>
  );
}
