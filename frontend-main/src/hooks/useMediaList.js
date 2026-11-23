import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";

/**
 * useMediaList — the fetch/pagination/error engine behind every browse page.
 *
 * Before this existed, each of the nine list pages repeated the same effect and
 * every one of them wrote `.catch(() => setIsLoading(false))`, so a 500 was
 * indistinguishable from an empty catalogue and there was no retry anywhere.
 *
 * Returns { items, total, isLoading, error, reload }.
 */
export default function useMediaList({
  base,
  path,
  params = {},
  pick = (data) => data.results || data.movies || data.tv_shows || [],
  totalOf = (data) => data.total_count ?? 0,
  enabled = true,
}) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(enabled);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);

  // Serialised so the effect does not re-run on every inline-object identity.
  const paramsKey = JSON.stringify(params);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      return undefined;
    }
    const requestId = ++requestRef.current;
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);

    axios
      .get(`${base}${path}`, { params, signal: controller.signal })
      .then(({ data }) => {
        if (requestId !== requestRef.current) return;
        const list = pick(data);
        setItems(Array.isArray(list) ? list : []);
        setTotal(Number(totalOf(data, list)) || 0);
        setIsLoading(false);
      })
      .catch((err) => {
        if (requestId !== requestRef.current) return;
        if (axios.isCancel(err) || err.name === "CanceledError") return;
        setItems([]);
        setTotal(0);
        setError(err);
        setIsLoading(false);
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, path, paramsKey, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { items, total, isLoading, error, reload };
}
