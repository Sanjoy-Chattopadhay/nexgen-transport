import { useState } from 'react';

export function usePagination(defaultSort = 'total_trips', defaultOrder: 'asc' | 'desc' = 'desc') {
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState(defaultSort);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(defaultOrder);

  const onSort = (col: string) => {
    if (col === sortBy) {
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortOrder('desc');
    }
    setPage(1);
  };

  const onSearch = (val: string) => {
    setSearch(val);
    setPage(1);
  };

  return { page, setPage, limit, search, onSearch, sortBy, sortOrder, onSort };
}
