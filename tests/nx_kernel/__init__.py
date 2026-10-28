# Named nx_kernel rather than narranexus on purpose: pytest inserts tests/ into
# sys.path, and a tests/narranexus/ package would shadow the real src/narranexus
# package during collection.
