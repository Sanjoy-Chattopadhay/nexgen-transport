import { backendApi } from './api';

export interface Consignor {
  id: number;
  name: string;
  trip_count: number;
}

export const listConsignors = () => backendApi.get<{ data: Consignor[]; total: number }>('/consignors');
export const getConsignor = (id: number | string) => backendApi.get<Consignor>(`/consignors/${id}`);
