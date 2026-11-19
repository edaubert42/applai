# Bundled geography

`idf_communes.csv`: the 1,266 communes of Île-de-France (departments 75, 77, 78, 91, 92, 93, 94, 95), one row per commune: INSEE code, name, department, postcodes separated by spaces. Paris's arrondissements are written as a range, `75001-75020 75116`.

- Source: API Découpage administratif, `https://geo.api.gouv.fr/departements/<department>/communes?fields=nom,code,codesPostaux,codeDepartement`, fetched on 2026-11-19.
- Licence: Licence Ouverte / Open Licence 2.0 (Etalab).
