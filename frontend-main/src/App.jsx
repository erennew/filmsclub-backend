import Home from "./pages/Home";
import MovieDetails from "./pages/MovieDetails";
import TvDetails from "./pages/TvDetails";
import Movies from "./pages/Movies";
import Series from "./pages/Series";
import SimilarMov from "./pages/SimilarMov";
import SimilarSeries from "./pages/SimilarSeries";
import SearResults from "./pages/SearchResults";
// import Token from "./pages/Token";
import NotFoundPage from "./pages/NotFound";
// import Login from "./pages/Login";

import Nav from "./components/Nav";
import Footer from "./components/Footer";
// import PrivateRoute from "./components/PrivateRoute";

// import { AuthProvider } from "./context/AuthContext";

import ReactGa from "react-ga";
import { useEffect } from "react";
// import { ToastContainer } from "react-toastify";
import { BrowserRouter, Route, Routes } from "react-router-dom";


function App() {
  ReactGa.initialize("G-JDFS7KRV40");
  useEffect(() => {
    ReactGa.pageview(window.location.pathname + window.location.search);
  }, []);

  return (

    <BrowserRouter>
      <Nav />
      <div className="p-3 md:p-10">

        <Routes>
          <Route path="/" element={<Home />} />
          <Route
            path="mov/:movieID"
            element={<MovieDetails />}
          />
          <Route
            path="ser/:seriesID"
            element={<TvDetails />}
          />
          <Route
            path="/Movies"
            element={<Movies />}
          />
          <Route
            path="/Series"
            element={<Series />}
          />
          <Route
            path="*"
            element={<NotFoundPage />}
          />
          <Route
            path="/similarMov/:movieID"
            element={<SimilarMov />}
          />
          <Route
            path="/similarSeries/:seriesID"
            element={<SimilarSeries />}
          />

          <Route
            path="/search/:searchResult"
            element={<SearResults />}
          />
        </Routes>
      </div>
      <Footer />
    </BrowserRouter>
  );
}

export default App;
