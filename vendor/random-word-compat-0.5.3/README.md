# `random_word` compatibility package

This project-authored package replaces the MIT-declared `random_word` 0.5.2
crate because its exact release source has no package-level primary license
text. The only API required by the PG16 release graph is
`all(Lang::En)`, used by Lance 7.0.0 data generation.

The implementation contains no code from the replaced crate. It reads the
English word data from `words.txt`, which is an exact Brotli expansion of the
upstream 0.5.2 `src/br/en.br` asset at commit
`dc1cab8d7951ff4285ea9158c8954d663fdd6fbf`. The upstream source labels that
word-list data public domain; its notice is retained in
`NOTICE-English-word-list.txt`. The compatibility implementation is covered by
the project Apache-2.0 license in `LICENSE`.

The package intentionally implements only the API present in the selected
dependency graph. Reassess the source and add a verified interface test before
expanding it. Remove this patch only after an upstream replacement has a
package-level license text and the PG16 release graph and regression checks
confirm that the replacement preserves the required data-generation behavior.
