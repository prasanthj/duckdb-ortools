# Community distributions contain one file. Build the same pinned minimal
# dependency policy as the standalone bundle, but link it statically.
find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
set(ortools_dependency_args
  "--cmake-arg=-DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}"
  "--cmake-arg=-DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}"
  "--cmake-arg=-DCMAKE_C_FLAGS=${CMAKE_C_FLAGS}"
  "--cmake-arg=-DCMAKE_CXX_FLAGS=${CMAKE_CXX_FLAGS}")
if(APPLE)
  list(APPEND ortools_dependency_args
    "--cmake-arg=-DCMAKE_OSX_ARCHITECTURES=${CMAKE_OSX_ARCHITECTURES}"
    "--cmake-arg=-DCMAKE_OSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}"
    "--cmake-arg=-DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}")
endif()
execute_process(
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/dependencies.py"
    --static ${ortools_dependency_args}
  COMMAND_ERROR_IS_FATAL ANY)
list(PREPEND CMAKE_PREFIX_PATH "${CMAKE_CURRENT_SOURCE_DIR}/vendor/ortools-minimal-static")
set(ortools_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/ortools-minimal-static/lib/cmake/ortools")
set(ZLIB_USE_STATIC_LIBS ON)
# BZip2 installs both variants and names its static archive bz2_static.
set(BZIP2_LIBRARY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/vendor/ortools-minimal-static/lib/libbz2_static.a"
  CACHE FILEPATH "Pinned static BZip2 library" FORCE)
set(BZIP2_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/ortools-minimal-static/include"
  CACHE PATH "Pinned BZip2 headers" FORCE)
