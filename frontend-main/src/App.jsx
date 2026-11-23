import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { ToastContainer } from "react-toastify";
import "react-toastify/dist/ReactToastify.css";

import Nav from "./components/Nav";
import PWAInstallPrompt from "./components/PWAInstallPrompt";
import Footer from "./components/Footer";
import ScrollManager from "./components/ScrollManager";
import ErrorBoundary from "./components/ErrorBoundary";

import { Analytics } from "@vercel/analytics/react";

/*
 * Routes are lazy-loaded so the homepage does not download Plyr, Swiper and the
 * whole admin console before first paint. Every chunk below is its own file;
 * the shared chrome (Nav/Footer) stays in the entry bundle.
 */
const Home = lazy(() => import("./pages/Home"));
const MovieDetails = lazy(() => import("./pages/MovieDetails"));
const TvDetails = lazy(() => import("./pages/TvDetails"));
const Movies = lazy(() => import("./pages/Movies"));
const Series = lazy(() => import("./pages/Series"));
const TopRatedMovies = lazy(() => import("./pages/TopRatedMovies"));
const TopRatedSeries = lazy(() => import("./pages/TopRatedSeries"));
const MostViewed = lazy(() => import("./pages/MostViewed"));
const Anime = lazy(() => import("./pages/Anime"));
const KDrama = lazy(() => import("./pages/KDrama"));
const SimilarMov = lazy(() => import("./pages/SimilarMov"));
const SimilarSeries = lazy(() => import("./pages/SimilarSeries"));
const SearResults = lazy(() => import("./pages/SearchResults"));
const CastMedia = lazy(() => import("./pages/CastMedia"));
const GenrePage = lazy(() => import("./pages/GenrePage"));
const MyList = lazy(() => import("./pages/MyList"));
const NotFoundPage = lazy(() => import("./pages/NotFound"));
const AdminShell = lazy(() => import("./admin/AdminShell"));

/* Route-level fallback: reserves space so the shell does not jump. */
function RouteFallback() {
  return (
    <div className="py-16" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <div className="skeleton mx-auto h-6 w-40 rounded-[3px]" aria-hidden="true" />
      <div className="mx-auto mt-6 grid max-w-[1800px] grid-cols-3 gap-3 px-1 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-7">
        {Array.from({ length: 14 }).map((_, i) => (
          <div key={i} aria-hidden="true">
            <div className="skeleton aspect-[2/3] w-full rounded-[3px]" />
            <div className="skeleton mt-2 h-3 w-4/5 rounded-[2px]" />
          </div>
        ))}
      </div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Shell />
      </ErrorBoundary>
    </BrowserRouter>
  );
}

/* The admin console renders without the public site chrome (nav, footer, PWA prompt). */
function Shell() {
  const { pathname } = useLocation();
  // Splat parent so the console's relative child routes resolve under /admin.
  if (pathname === "/admin" || pathname.startsWith("/admin/")) {
    return (
      <>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/admin/*" element={<AdminShell />} />
          </Routes>
        </Suspense>
        {/* Single mount for the whole app. Previously Home, Login, MovieDetails
            and TvDetails each rendered their own <ToastContainer>, so toasts
            fired on one route never appeared on another and four copies of the
            toast region were in the accessibility tree at once. */}
        <ToastContainer
          position="bottom-right"
          autoClose={3200}
          newestOnTop
          closeOnClick
          pauseOnFocusLoss={false}
          theme="dark"
          style={{ fontSize: "0.8rem" }}
        />
      </>
    );
  }
  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-[3px] focus:bg-projector focus:px-4 focus:py-2 focus:text-sm focus:font-bold focus:text-ink"
      >
        Skip to content
      </a>
      <Nav />
      <PWAInstallPrompt />
      {/* Main Content - properly spaced for fixed nav and mobile bottom bar */}
      <div className="min-h-screen bg-ink">
        {/* Top padding clears the fixed nav (plus notch safe area);
            bottom padding clears the floating mobile dock.
            Both switch on `navlg` (990px), the same breakpoint the nav itself
            uses — with `md` here the dock vanished at 700px while this padding
            waited until 768px, leaving 68px of dead space and, below that, the
            dock overlapping the last row of cards. */}
        <main
          id="main-content"
          tabIndex={-1}
          className="pt-[calc(var(--safe-top)+4rem)] pb-28 navlg:pt-[calc(var(--safe-top)+5rem)] navlg:pb-10 px-3 sm:px-4 md:px-8 lg:px-12 xl:px-16 max-w-[1800px] mx-auto outline-none"
        >
          <ErrorBoundary compact>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/mov/:movieID" element={<MovieDetails />} />
                <Route path="/ser/:seriesID" element={<TvDetails />} />
                <Route path="/Movies" element={<Movies />} />
                <Route path="/Series" element={<Series />} />
                <Route path="/top-rated-movies" element={<TopRatedMovies />} />
                <Route path="/top-rated-series" element={<TopRatedSeries />} />
                <Route path="/most-viewed" element={<MostViewed />} />
                <Route path="/anime" element={<Anime />} />
                <Route path="/k-drama" element={<KDrama />} />
                <Route path="/similarMov/:movieID" element={<SimilarMov />} />
                <Route path="/similarSeries/:seriesID" element={<SimilarSeries />} />
                <Route path="/search/:searchResult" element={<SearResults />} />
                <Route path="/Cast/:castId/:castName" element={<CastMedia />} />
                <Route path="/genre/:mediaType/:genreName" element={<GenrePage />} />
                <Route path="/my-list" element={<MyList />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
      <Footer />
      <ScrollManager />
      <ToastContainer
        position="bottom-right"
        autoClose={3200}
        newestOnTop
        closeOnClick
        pauseOnFocusLoss={false}
        theme="dark"
        style={{ fontSize: "0.8rem" }}
      />
      <Analytics />
    </>
  );
}

export default App;
